@description('Existing Azure App Configuration store that holds authoritative TokenGov policy.')
param storeName string

@description('Object ID of the OIDC-federated GitHub Actions service principal.')
param publisherPrincipalId string

resource appConfig 'Microsoft.AppConfiguration/configurationStores@2024-05-01' existing = {
  name: storeName
}

var appConfigDataOwnerRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '5ae67dd6-50cb-40e7-96ff-dc2bfa4b606b'
)

resource publisherRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appConfig.id, publisherPrincipalId, appConfigDataOwnerRoleId)
  scope: appConfig
  properties: {
    description: 'Publishes reviewed TokenGov policy and append-only publication evidence.'
    principalId: publisherPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: appConfigDataOwnerRoleId
  }
}

output publisherRoleAssignmentId string = publisherRole.id
