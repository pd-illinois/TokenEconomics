// platform.bicep - promotes the in-process CONTROL PLANE to real Azure services.
// Deploys into rg-tokengov alongside the models. (Semantic cache stays in-process per
// decision - no Redis; APIM gateway handles routing + token caps + metrics.)
//
//   az deployment group create -g rg-tokengov -f infra/platform.bicep \
//     -p budgetContactEmail=<you@example.com>
//
// Adds:
//   * Azure Function (decision binding)  - the eval->enforce "hands" (productionizes decision.py)
//   * Storage account                    - Function runtime store
//   * Monitor action group               - fires the Function on an eval-regression alert
//   * Cost Management budget             - FinOps guardrail (productionizes finops.py)
//   * RBAC so the Function MI can WRITE knobs to App Configuration + call the judge model

@description('Location.')
param location string = resourceGroup().location

@description('Existing AI Services account (models) name.')
param accountName string = 'tokengov-aoai'

@description('Existing App Configuration store name.')
param appConfigName string = 'tokengov-aoai-appcfg'

@description('Existing Application Insights name.')
param appInsightsName string = 'tokengov-aoai-ai'

@description('Email for budget + action group notifications.')
param budgetContactEmail string

@description('Monthly budget in USD for this resource group.')
param budgetAmount int = 200

var suffix = uniqueString(resourceGroup().id)
var funcName = 'func-tokengov-${suffix}'
var storageName = 'sttokengov${substring(suffix, 0, 8)}'

// ---- existing resources we bind to ----
resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: accountName }
resource appConfig 'Microsoft.AppConfiguration/configurationStores@2023-03-01' existing = { name: appConfigName }
resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = { name: appInsightsName }

// ---- storage for the Function ----
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false }
}

// ---- consumption plan (Linux) ----
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'plan-tokengov-${suffix}'
  location: location
  sku: { name: 'Y1', tier: 'Dynamic' }
  kind: 'functionapp'
  properties: { reserved: true }
}

// ---- Function App (Python, decision binding) ----
resource func 'Microsoft.Web/sites@2023-12-01' = {
  name: funcName
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storage.listKeys().keys[0].value}' }
        { name: 'WEBSITE_CONTENTAZUREFILECONNECTIONSTRING', value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storage.listKeys().keys[0].value}' }
        { name: 'WEBSITE_CONTENTSHARE', value: toLower(funcName) }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'AZURE_APPCONFIG_ENDPOINT', value: appConfig.properties.endpoint }
        { name: 'AZURE_OPENAI_ENDPOINT', value: account.properties.endpoint }
      ]
    }
  }
}

// ---- RBAC: Function MI can WRITE knobs to App Configuration ----
var appConfigDataOwner = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5ae67dd6-50cb-40e7-96ff-dc2bfa4b606b')
resource funcAppConfigRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appConfig.id, func.id, appConfigDataOwner)
  scope: appConfig
  properties: {
    roleDefinitionId: appConfigDataOwner
    principalId: func.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---- RBAC: Function MI can call the judge model ----
var openAiUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
resource funcOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, func.id, openAiUser)
  scope: account
  properties: {
    roleDefinitionId: openAiUser
    principalId: func.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---- Monitor action group (fires the decision binding on an eval-regression alert) ----
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-tokengov-eval'
  location: 'global'
  properties: {
    groupShortName: 'tokengov'
    enabled: true
    emailReceivers: [
      { name: 'owner', emailAddress: budgetContactEmail, useCommonAlertSchema: true }
    ]
  }
}

// ---- Cost Management budget (FinOps guardrail) ----
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'budget-tokengov'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: '2026-07-01' }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        contactEmails: [ budgetContactEmail ]
        contactGroups: [ actionGroup.id ]
        thresholdType: 'Actual'
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        contactEmails: [ budgetContactEmail ]
        thresholdType: 'Forecasted'
      }
    }
  }
}

output functionName string = func.name
output functionPrincipalId string = func.identity.principalId
output functionHostname string = func.properties.defaultHostName
output actionGroupId string = actionGroup.id
