// apim.bicep - the GenAI gateway service (long-pole provision, ~40 min on Developer).
// Deployed first, bare; the AOAI backend + API + policies are applied after it's up
// (CLI, per the azure-aigateway skill) so the 40-min clock starts immediately.
//
//   az deployment group create -g rg-tokengov -f infra/apim.bicep \
//     -p publisherEmail=<you@example.com>

@description('APIM tier. Developer = cheapest/no-SLA, right for dev/test.')
@allowed([ 'Developer', 'BasicV2', 'StandardV2' ])
param sku string = 'Developer'

@description('Location (same as models for low hop latency).')
param location string = resourceGroup().location

param publisherEmail string
param publisherName string = 'TokenGov'
param apimName string = 'apim-tokengov-${uniqueString(resourceGroup().id)}'

resource apim 'Microsoft.ApiManagement/service@2023-05-01-preview' = {
  name: apimName
  location: location
  sku: { name: sku, capacity: 1 }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

output apimName string = apim.name
output apimPrincipalId string = apim.identity.principalId
output gatewayUrl string = apim.properties.gatewayUrl
