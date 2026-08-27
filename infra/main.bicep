// main.bicep - declarative infra for the live prototype.
// Deploy with Azure MCP (VS Code) or:
//   PRINCIPAL=$(az ad signed-in-user show --query id -o tsv)
//   az group create -n rg-tokengov -l eastus2
//   az deployment group create -g rg-tokengov -f infra/main.bicep \
//     -p principalId=$PRINCIPAL
//
// Model deployments MUST be serial (an account rejects parallel deploys), so each
// depends on the previous. Verify model names/versions for your region before deploying.

@description('Location. Model Router is GA in eastus2 and swedencentral.')
param location string = 'eastus2'

@description('Cognitive Services (AI Services) account name — also the custom subdomain.')
param accountName string = 'tokengov-aoai'

@description('Object id of the user/principal to grant data-plane access (Entra ID).')
param principalId string

@description('Object ID of the Studio managed identity or user that reads TokenGov policy.')
param policyReaderPrincipalId string = principalId

@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param policyReaderPrincipalType string = 'User'

@description('Optional object ID allowed to publish policy revisions. Keep separate from the runtime reader.')
param policyAdministratorPrincipalId string = ''

@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param policyAdministratorPrincipalType string = 'User'

param policyKey string = 'tokengov:policy'
param policyLabel string = 'production'

param cheapModel string = 'gpt-5-nano'
param cheapVersion string = '2025-08-07'
param premiumModel string = 'gpt-5'
param premiumVersion string = '2025-08-07'
param routerVersion string = '2025-11-18'
param embedModel string = 'text-embedding-3-small'
param embedVersion string = '1'

@description('SKU for the cheap/premium chat deployments. gpt-5 family is GlobalStandard in most regions.')
param chatSku string = 'GlobalStandard'

@description('SKU for the embedding deployment. text-embedding-3-small is GlobalStandard-only in some regions (e.g. swedencentral).')
param embedSku string = 'GlobalStandard'

param deployAppConfig bool = true
param deployAppInsights bool = true

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: accountName   // required for Entra ID / keyless auth
    publicNetworkAccess: 'Enabled'
  }
}

resource cheap 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: 'gpt-cheap'
  sku: { name: chatSku, capacity: 50 }
  properties: { model: { format: 'OpenAI', name: cheapModel, version: cheapVersion } }
}

resource premium 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: 'gpt-premium'
  dependsOn: [ cheap ]
  sku: { name: chatSku, capacity: 50 }
  properties: { model: { format: 'OpenAI', name: premiumModel, version: premiumVersion } }
}

resource router 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: 'model-router'
  dependsOn: [ premium ]
  sku: { name: 'GlobalStandard', capacity: 50 }
  properties: { model: { format: 'OpenAI', name: 'model-router', version: routerVersion } }
}

resource embed 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: 'text-embed'
  dependsOn: [ router ]
  sku: { name: embedSku, capacity: 50 }
  properties: { model: { format: 'OpenAI', name: embedModel, version: embedVersion } }
}

// RBAC: "Cognitive Services OpenAI User" (fixed built-in role GUID)
var openAiUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')

resource roleAssign 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, openAiUserRoleId)
  scope: account
  properties: {
    roleDefinitionId: openAiUserRoleId
    principalId: principalId
    principalType: 'User'
  }
}

resource appConfig 'Microsoft.AppConfiguration/configurationStores@2023-03-01' = if (deployAppConfig) {
  name: '${accountName}-appcfg'
  location: location
  sku: { name: 'standard' }
}

resource tokenGovPolicy 'Microsoft.AppConfiguration/configurationStores/keyValues@2023-03-01' = if (deployAppConfig) {
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

resource appConfigPolicyReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAppConfig) {
  name: guid(appConfig.id, policyReaderPrincipalId, appConfigDataReaderRoleId)
  scope: appConfig
  properties: {
    roleDefinitionId: appConfigDataReaderRoleId
    principalId: policyReaderPrincipalId
    principalType: policyReaderPrincipalType
  }
}

resource appConfigPolicyAdministrator 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAppConfig && !empty(policyAdministratorPrincipalId)) {
  name: guid(appConfig.id, policyAdministratorPrincipalId, appConfigDataOwnerRoleId)
  scope: appConfig
  properties: {
    roleDefinitionId: appConfigDataOwnerRoleId
    principalId: policyAdministratorPrincipalId
    principalType: policyAdministratorPrincipalType
  }
}

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (deployAppInsights) {
  name: '${accountName}-law'
  location: location
  properties: { sku: { name: 'PerGB2018' } }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = if (deployAppInsights) {
  name: '${accountName}-ai'
  location: location
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: law.id }
}

// Feed these into .env (see HANDOFF.md).
output endpoint string = account.properties.endpoint
output deploymentCheap string = cheap.name
output deploymentPremium string = premium.name
output deploymentRouter string = router.name
output deploymentEmbedding string = embed.name
output appConfigEndpoint string = appConfig.?properties.?endpoint ?? ''
output tokenGovPolicyKey string = deployAppConfig ? policyKey : ''
output tokenGovPolicyLabel string = deployAppConfig ? policyLabel : ''
output appInsightsConnectionString string = appInsights.?properties.?ConnectionString ?? ''
