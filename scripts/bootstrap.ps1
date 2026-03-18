param(
  [Parameter(Mandatory = $true)]
  [string]$SubscriptionId,

  [Parameter(Mandatory = $true)]
  [string]$TenantId,

  [Parameter(Mandatory = $true)]
  [string]$Repository, # owner/repo, e.g. sujithq/updates

  [string]$Branch = "main",
  [string]$AppDisplayName = "updates-azd-provision-bootstrap",
  [string]$FederatedCredentialName = "github-oidc-main"
)

$ErrorActionPreference = "Stop"

function Invoke-AzCli {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$Args,

    [switch]$ParseJson,

    [string]$FailureMessage = "Azure CLI command failed"
  )

  $output = az @Args 2>&1
  if ($LASTEXITCODE -ne 0) {
    $text = ($output | Out-String).Trim()
    if ($text) {
      throw "$FailureMessage`n$text"
    }
    throw $FailureMessage
  }

  if ($ParseJson) {
    $text = ($output | Out-String).Trim()
    if (-not $text) {
      return $null
    }
    return $text | ConvertFrom-Json
  }

  return ($output | Out-String).Trim()
}

if ($Repository -notmatch '^[^/]+/[^/]+$') {
  throw "Repository must be in owner/repo format. Received: '$Repository'"
}

if ([string]::IsNullOrWhiteSpace($Branch)) {
  throw "Branch cannot be empty."
}

Write-Host "Checking Azure CLI authentication..."
try {
  Invoke-AzCli -Args @('account', 'show', '--output', 'none') -FailureMessage "Azure CLI is not authenticated."
} catch {
  throw "Azure CLI is not authenticated or needs reauthentication. Run: az login --tenant $TenantId"
}

Write-Host "Setting Azure subscription context..."
Invoke-AzCli -Args @('account', 'set', '--subscription', $SubscriptionId, '--output', 'none') -FailureMessage "Failed to set Azure subscription context."

# 1) Create or reuse app registration
Write-Host "Resolving app registration '$AppDisplayName'..."
$app = Invoke-AzCli -Args @('ad', 'app', 'list', '--display-name', $AppDisplayName, '--query', '[0]', '-o', 'json') -ParseJson -FailureMessage "Failed to query app registration."
if (-not $app) {
  Write-Host "App not found. Creating..."
  $app = Invoke-AzCli -Args @('ad', 'app', 'create', '--display-name', $AppDisplayName, '-o', 'json') -ParseJson -FailureMessage "Failed to create app registration."
} else {
  Write-Host "App already exists."
}

$appId = $app.appId
$appObjectId = $app.id
if ([string]::IsNullOrWhiteSpace($appId) -or [string]::IsNullOrWhiteSpace($appObjectId)) {
  throw "Failed to resolve app registration identifiers."
}

# 2) Create or ensure service principal
Write-Host "Ensuring service principal exists..."
$sp = Invoke-AzCli -Args @('ad', 'sp', 'list', '--filter', "appId eq '$appId'", '--query', '[0]', '-o', 'json') -ParseJson -FailureMessage "Failed to query service principal."
if (-not $sp) {
  $sp = Invoke-AzCli -Args @('ad', 'sp', 'create', '--id', $appId, '-o', 'json') -ParseJson -FailureMessage "Failed to create service principal."
  Write-Host "Service principal created."
} else {
  Write-Host "Service principal already exists."
}
$spObjectId = $sp.id
if ([string]::IsNullOrWhiteSpace($spObjectId)) {
  throw "Failed to resolve service principal object ID."
}

# 3) Configure federated credential for GitHub OIDC
$subject = "repo:$Repository:ref:refs/heads/$Branch"
Write-Host "Configuring federated credential subject: $subject"

$existingFc = Invoke-AzCli -Args @('ad', 'app', 'federated-credential', 'list', '--id', $appObjectId, '--query', "[?name=='$FederatedCredentialName'] | [0]", '-o', 'json') -ParseJson -FailureMessage "Failed to query federated credentials."

if (-not $existingFc) {
  $tmp = New-TemporaryFile
  @"
{
  "name": "$FederatedCredentialName",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$subject",
  "description": "GitHub Actions OIDC for $Repository ($Branch)",
  "audiences": [ "api://AzureADTokenExchange" ]
}
"@ | Set-Content -Path $tmp -Encoding UTF8

  Invoke-AzCli -Args @('ad', 'app', 'federated-credential', 'create', '--id', $appObjectId, '--parameters', $tmp) -FailureMessage "Failed to create federated credential." | Out-Null
  Remove-Item $tmp -Force
  Write-Host "Federated credential created."
} else {
  Write-Host "Federated credential already exists. Updating..."
  $tmp = New-TemporaryFile
  @"
{
  "name": "$FederatedCredentialName",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$subject",
  "description": "GitHub Actions OIDC for $Repository ($Branch)",
  "audiences": [ "api://AzureADTokenExchange" ]
}
"@ | Set-Content -Path $tmp -Encoding UTF8

  Invoke-AzCli -Args @('ad', 'app', 'federated-credential', 'update', '--id', $appObjectId, '--federated-credential-id', $FederatedCredentialName, '--parameters', $tmp) -FailureMessage "Failed to update federated credential." | Out-Null
  Remove-Item $tmp -Force
  Write-Host "Federated credential updated."
}

# 4) Assign required roles at subscription scope
$scope = "/subscriptions/$SubscriptionId"
$roles = @("Contributor", "User Access Administrator")

foreach ($role in $roles) {
  $exists = Invoke-AzCli -Args @(
    'role', 'assignment', 'list',
    '--assignee-object-id', $spObjectId,
    '--scope', $scope,
    '--query', "[?roleDefinitionName=='$role'] | length(@)",
    '-o', 'tsv'
  ) -FailureMessage "Failed to query role assignment for role '$role'."

  if ($exists -eq "0") {
    Write-Host "Assigning role '$role' on $scope..."
    Invoke-AzCli -Args @(
      'role', 'assignment', 'create',
      '--assignee-object-id', $spObjectId,
      '--assignee-principal-type', 'ServicePrincipal',
      '--role', $role,
      '--scope', $scope
    ) -FailureMessage "Failed to assign role '$role'." | Out-Null
  } else {
    Write-Host "Role '$role' already assigned."
  }
}

Write-Host ""
Write-Host "Done. Set these GitHub Secrets:"
Write-Host "AZURE_PROVISION_CLIENT_ID = $appId"
Write-Host "AZURE_TENANT_ID           = $TenantId"
Write-Host "AZURE_SUBSCRIPTION_ID     = $SubscriptionId"
Write-Host ""
Write-Host "Workflow OIDC subject expected:"
Write-Host $subject