targetScope = 'subscription'

@description('Resource ID of the existing evidence storage account.')
param storageAccountId string

@description('UTC schedule start. Cost Management requires a future timestamp.')
param scheduleStart string

@description('UTC schedule end.')
param scheduleEnd string

@description('Azure region for the export system-assigned identity.')
param location string = 'eastus2'

@description('Name of the recurring subscription cost export.')
param exportName string = 'tokengov-actual-cost-daily'

resource actualCostExport 'Microsoft.CostManagement/exports@2025-03-01' = {
  name: exportName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    exportDescription: 'Daily subscription ActualCost evidence for TokenEconomics reconciliation.'
    format: 'Csv'
    compressionMode: 'gzip'
    dataOverwriteBehavior: 'CreateNewReport'
    partitionData: true
    definition: {
      type: 'ActualCost'
      timeframe: 'MonthToDate'
      dataSet: {
        granularity: 'Daily'
      }
    }
    deliveryInfo: {
      destination: {
        type: 'AzureBlob'
        resourceId: storageAccountId
        container: 'cost-exports'
        rootFolderPath: 'tokengov/actual-cost'
      }
    }
    schedule: {
      status: 'Active'
      recurrence: 'Daily'
      recurrencePeriod: {
        from: scheduleStart
        to: scheduleEnd
      }
    }
  }
}

output exportId string = actualCostExport.id
output exportPrincipalId string = actualCostExport.identity.principalId
