// Evaluation-only adaptation of microsoft-foundry/foundry-samples template 15a.
// Source revision and deployment evidence are recorded separately; no existing account is redeployed.
targetScope = 'resourceGroup'

param location string = 'eastus2'
param accountName string = 'ai-eval-xbk6ickycmp22'
param projectName string = 'ai-project-tokengov-eval'
param storageAccountName string = 'stxbk6ickycmp22'
param vnetName string = 'vnet-tokengov-eval'
param operatorPrincipalId string
@description('Single public IPv4 address allowed to submit evaluations; storage remains private.')
param operatorIpAddress string
param judgeDeploymentName string = 'rag-agent-runtime-gpt-4-1-mini'
param judgeCapacity int = 100
param vnetAddressPrefix string = '172.28.240.0/23'
param evaluationSubnetPrefix string = '172.28.240.0/24'
param privateEndpointSubnetPrefix string = '172.28.241.0/24'

var tags = {
  workload: 'tokengov-evaluation'
  environment: 'measurement'
}
var foundryUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
var blobDataOwnerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
var dnsZoneNames = [
  'privatelink.services.ai.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.cognitiveservices.azure.com'
  'privatelink.blob.${environment().suffixes.storage}'
]

resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' existing = {
  name: storageAccountName
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        name: 'evaluation'
        properties: {
          addressPrefix: evaluationSubnetPrefix
          defaultOutboundAccess: false
          delegations: [
            {
              name: 'foundry-evaluation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          defaultOutboundAccess: false
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      virtualNetworkRules: []
      ipRules: [
        { value: operatorIpAddress }
      ]
    }
    networkInjections: [
      {
        scenario: 'agent'
        subnetArmId: '${vnet.id}/subnets/evaluation'
        useMicrosoftManagedNetwork: false
      }
    ]
  }
}

resource judge 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: judgeDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: judgeCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1-mini'
      version: '2025-04-14'
    }
    raiPolicyName: 'Microsoft.DefaultV2'
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource zones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for name in dnsZoneNames: {
  name: name
  location: 'global'
  tags: tags
}]

resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for (name, index) in dnsZoneNames: {
  name: '${name}/${vnetName}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
  dependsOn: [zones[index]]
}]

resource accountEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-tokengov-eval-foundry'
  location: location
  tags: tags
  properties: {
    subnet: { id: '${vnet.id}/subnets/private-endpoints' }
    privateLinkServiceConnections: [
      {
        name: 'foundry-evaluation'
        properties: {
          privateLinkServiceId: account.id
          groupIds: ['account']
        }
      }
    ]
  }
  // Account child provisioning can temporarily return the parent to Accepted.
  dependsOn: [
    judge
    accountConnection
  ]
}

resource accountDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: accountEndpoint
  name: 'foundry'
  properties: {
    privateDnsZoneConfigs: [for index in range(0, 3): {
      name: 'foundry-${index}'
      properties: { privateDnsZoneId: zones[index].id }
    }]
  }
  dependsOn: [links]
}

resource storageEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-tokengov-eval-storage'
  location: location
  tags: tags
  properties: {
    subnet: { id: '${vnet.id}/subnets/private-endpoints' }
    privateLinkServiceConnections: [
      {
        name: 'evaluation-storage'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource storageDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: storageEndpoint
  name: 'blob'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob'
        properties: { privateDnsZoneId: zones[3].id }
      }
    ]
  }
  dependsOn: [links]
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'TokenGov private evaluation'
    description: 'Captured-response evaluation only; source RAG agent remains in its original account.'
  }
  dependsOn: [
    accountDns
    storageDns
  ]
}

var storageConnection = {
  category: 'AzureStorageAccount'
  target: storage.properties.primaryEndpoints.blob
  authType: 'AAD'
  isSharedToAll: true
  metadata: {
    ApiType: 'Azure'
    ResourceId: storage.id
    location: storage.location
  }
}

resource accountConnection 'Microsoft.CognitiveServices/accounts/connections@2025-04-01-preview' = {
  parent: account
  name: 'evaluation-storage'
  properties: storageConnection
}

resource projectFoundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, project.id, foundryUserRole)
  scope: account
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryUserRole
  }
}

resource operatorFoundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, operatorPrincipalId, foundryUserRole)
  scope: account
  properties: {
    principalId: operatorPrincipalId
    principalType: 'User'
    roleDefinitionId: foundryUserRole
  }
}

resource accountStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, account.id, blobDataOwnerRole)
  scope: storage
  properties: {
    principalId: account.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobDataOwnerRole
  }
}

resource projectStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, project.id, blobDataOwnerRole)
  scope: storage
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobDataOwnerRole
  }
}

output evaluationProjectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}'
output evaluationProjectId string = project.id
output evaluationProjectPrincipalId string = project.identity.principalId
output evaluationAccountPrincipalId string = account.identity.principalId
output evaluationSubnetId string = '${vnet.id}/subnets/evaluation'
output storagePrivateEndpointId string = storageEndpoint.id
