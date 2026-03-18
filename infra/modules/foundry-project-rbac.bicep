targetScope = 'resourceGroup'

@description('Foundry account name that contains the target project.')
param foundryAccountName string

@description('Foundry project name.')
param foundryProjectName string

@description('Principal ID of the GitHub user-assigned managed identity.')
param githubPrincipalId string

var azureAiUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundryAccount
  name: foundryProjectName
}

resource foundryProjectAzureAiUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryProject.id, githubPrincipalId, azureAiUserRoleId)
  scope: foundryProject
  properties: {
    principalId: githubPrincipalId
    roleDefinitionId: azureAiUserRoleId
    principalType: 'ServicePrincipal'
  }
}

output foundryProjectId string = foundryProject.id
output foundryProjectPrincipalId string = foundryProject.identity.principalId
