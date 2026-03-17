targetScope = 'resourceGroup'

@description('azd environment name.')
param environmentName string

@description('Azure location for provisioned resources.')
param location string = resourceGroup().location

@description('Globally unique Azure AI Search service name.')
param searchServiceName string

@description('Azure AI Search SKU.')
@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param searchSku string = 'standard'

@description('Semantic search tier for Azure AI Search.')
@allowed([
  'disabled'
  'free'
  'standard'
])
param semanticSearch string = 'standard'

@description('Index name used by the feed indexing script.')
param searchIndexName string = 'azure-news-feed'

@description('User-assigned identity name for GitHub Actions OIDC login.')
param githubIdentityName string = '${environmentName}-github-mi'

@description('GitHub repository in owner/name format. Leave empty to skip federated credential creation.')
param githubRepository string = ''

@description('Git branch used by the GitHub Actions workflow subject.')
param githubBranch string = 'main'

@description('Existing Foundry project endpoint to export into azd environment values.')
param foundryProjectEndpoint string = ''

@description('Foundry model deployment name to export into azd environment values.')
param foundryModelDeploymentName string = 'gpt-5.4'

var tags = {
  'azd-env-name': environmentName
  app: 'updates'
  workload: 'feed-ingestion'
}

var githubSubject = empty(githubRepository) ? '' : 'repo:${githubRepository}:ref:refs/heads/${githubBranch}'
var searchServiceContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
var searchIndexDataContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')

resource githubIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: githubIdentityName
  location: location
  tags: tags
}

resource githubFederatedCredential 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2024-11-30' = if (!empty(githubRepository)) {
  parent: githubIdentity
  name: 'github-actions'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: githubSubject
    audiences: [
      'api://AzureADTokenExchange'
    ]
  }
}

resource searchService 'Microsoft.Search/searchServices@2025-02-01-preview' = {
  name: searchServiceName
  location: location
  sku: {
    name: searchSku
  }
  tags: tags
  properties: {
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
    disableLocalAuth: false
    hostingMode: 'default'
    partitionCount: 1
    publicNetworkAccess: 'Enabled'
    replicaCount: 1
    semanticSearch: semanticSearch
  }
}

resource searchServiceContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, githubIdentity.id, searchServiceContributorRoleId)
  scope: searchService
  properties: {
    principalId: githubIdentity.properties.principalId
    roleDefinitionId: searchServiceContributorRoleId
    principalType: 'ServicePrincipal'
  }
}

resource searchIndexDataContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, githubIdentity.id, searchIndexDataContributorRoleId)
  scope: searchService
  properties: {
    principalId: githubIdentity.properties.principalId
    roleDefinitionId: searchIndexDataContributorRoleId
    principalType: 'ServicePrincipal'
  }
}

output AZURE_CLIENT_ID string = githubIdentity.properties.clientId
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_SUBSCRIPTION_ID string = subscription().subscriptionId
output AZURE_SEARCH_ENDPOINT string = 'https://${searchService.name}.search.windows.net'
output AZURE_SEARCH_INDEX string = searchIndexName
@secure()
output AZURE_SEARCH_KEY string = searchService.listAdminKeys().primaryKey
output FOUNDRY_PROJECT_ENDPOINT string = foundryProjectEndpoint
output FOUNDRY_MODEL_DEPLOYMENT_NAME string = foundryModelDeploymentName
output GITHUB_OIDC_SUBJECT string = githubSubject
