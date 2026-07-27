<#
Deploy only the Azure App Configuration resources required for TokenGov policy.

Run from 06_prototype:
  pwsh infra/provision-policy.ps1 -SubscriptionId <id>
#>

[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [string]$SubscriptionId,
  [string]$ResourceGroup = 'rg-tokengov',
  [string]$StoreName = 'tokengov-aoai-appcfg',
  [string]$PolicyLabel = 'production',
  [string]$PolicyReaderPrincipalId = '',
  [ValidateSet('User', 'ServicePrincipal', 'Group')]
  [string]$PolicyReaderPrincipalType = 'User',
  [string]$PolicyAdministratorPrincipalId = '',
  [ValidateSet('User', 'ServicePrincipal', 'Group')]
  [string]$PolicyAdministratorPrincipalType = 'User'
)

$ErrorActionPreference = 'Stop'

az account set --subscription $SubscriptionId
if ($LASTEXITCODE -ne 0) { throw "Cannot select subscription $SubscriptionId" }

az appconfig show --resource-group $ResourceGroup --name $StoreName --output none
if ($LASTEXITCODE -ne 0) {
  throw "Existing App Configuration store $StoreName was not found in $ResourceGroup"
}

$signedInUserId = az ad signed-in-user show --query id -o tsv
if (-not $PolicyAdministratorPrincipalId) { $PolicyAdministratorPrincipalId = $signedInUserId }

$deploymentName = "tokengov-policy-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))"
$outputs = az deployment group create `
  --name $deploymentName `
  --resource-group $ResourceGroup `
  --template-file "$PSScriptRoot/tokengov-policy.bicep" `
  --parameters `
    storeName=$StoreName `
    policyLabel=$PolicyLabel `
    policyReaderPrincipalId=$PolicyReaderPrincipalId `
    policyReaderPrincipalType=$PolicyReaderPrincipalType `
    policyAdministratorPrincipalId=$PolicyAdministratorPrincipalId `
    policyAdministratorPrincipalType=$PolicyAdministratorPrincipalType `
  --query properties.outputs `
  --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'TokenGov policy deployment failed' }

$envFile = Join-Path (Split-Path -Parent $PSScriptRoot) '.env'
$existing = if (Test-Path $envFile) { Get-Content $envFile } else { @() }
$managedNames = @(
  'TOKENGOV_POLICY_SOURCE',
  'AZURE_APPCONFIG_ENDPOINT',
  'TOKENGOV_POLICY_KEY',
  'TOKENGOV_POLICY_LABEL'
)
$preserved = $existing | Where-Object {
  $line = $_
  -not ($managedNames | Where-Object { $line -match "^$([regex]::Escape($_))=" })
}
$policyEnvironment = @(
  'TOKENGOV_POLICY_SOURCE=azure',
  "AZURE_APPCONFIG_ENDPOINT=$($outputs.endpoint.value)",
  "TOKENGOV_POLICY_KEY=$($outputs.policyKey.value)",
  "TOKENGOV_POLICY_LABEL=$($outputs.policyLabel.value)"
)
@($preserved + $policyEnvironment) | Set-Content -Path $envFile -Encoding utf8

Write-Host "TokenGov policy deployed to $($outputs.endpoint.value)"
Write-Host "Policy identity: $($outputs.policyKey.value) / $($outputs.policyLabel.value)"
Write-Host "Studio policy environment written to $envFile"