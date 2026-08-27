@description('Name of an existing Azure App Configuration store in this resource group.')
param storeName string

@description('Optional object ID of the hosted Studio managed identity that reads TokenGov policy.')
param policyReaderPrincipalId string = ''

@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param policyReaderPrincipalType string = 'User'

@description('Optional object ID allowed to publish policy revisions. Keep separate from the runtime reader in production.')
param policyAdministratorPrincipalId string = ''

@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param policyAdministratorPrincipalType string = 'User'

param policyKey string = 'tokengov:policy'
param policyLabel string = 'production'

resource appConfig 'Microsoft.AppConfiguration/configurationStores@2023-03-01' existing = {
  name: storeName
}

resource tokenGovPolicy 'Microsoft.AppConfiguration/configurationStores/keyValues@2023-03-01' = {
  parent: appConfig
  name: '${policyKey}$${policyLabel}'
  properties: {
    contentType: 'application/json'
    value: loadTextContent('./policies/production.json')
    tags: {
      authority: 'TokenGov'
      environment: policyLabel
      managedBy: 'Bicep'
    }
  }
}

var appConfigDataReaderRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '516239f1-63e1-4d78-a4de-a74fb236a071c'
)
var appConfigDataOwnerRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '5ae67dd6-50cb-40e7-96ff-dc2bfa4b606b'
)

resource policyReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(policyReaderPrincipalId)) {
  name: guid(appConfig.id, policyReaderPrincipalId, appConfigDataReaderRoleId)
  scope: appConfig
  properties: {
    roleDefinitionId: appConfigDataReaderRoleId
    principalId: policyReaderPrincipalId
    principalType: policyReaderPrincipalType
  }
}

resource policyAdministrator 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(policyAdministratorPrincipalId)) {
  name: guid(appConfig.id, policyAdministratorPrincipalId, appConfigDataOwnerRoleId)
  scope: appConfig
  properties: {
    roleDefinitionId: appConfigDataOwnerRoleId
    principalId: policyAdministratorPrincipalId
    principalType: policyAdministratorPrincipalType
  }
}

output endpoint string = appConfig.properties.endpoint
output policyKey string = policyKey
output policyLabel string = policyLabel
output policyResourceId string = tokenGovPolicy.id
