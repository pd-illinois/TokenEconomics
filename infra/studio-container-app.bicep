@description('Deployment location. Use the existing TokenEconomics resource group location.')
param location string = resourceGroup().location

@description('Container image. The first deployment may use the public placeholder image.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Authoritative TokenGov policy label.')
param policyLabel string = 'te003-live-v2'

@description('GitHub Actions workflow page used to submit reviewed TokenGov policy publication.')
param policyApprovalUrl string = 'https://github.com/pd-illinois/TokenEconomics/actions/workflows/publish-tokengov-policy.yml'

@description('Protected GitHub environment that approves TokenGov policy publication.')
param policyApprovalEnvironment string = 'tokengov-production'

@description('GitHub App ID used only for policy review pull requests.')
param policyReviewGitHubAppId string = ''

@description('GitHub App installation ID for the TokenEconomics repository.')
param policyReviewGitHubInstallationId string = ''

@description('Repository receiving versioned policy review pull requests.')
param policyReviewGitHubRepository string = 'pd-illinois/TokenEconomics'

@description('GitHub base branch receiving approved policy reviews.')
param policyReviewGitHubBaseBranch string = 'main'

@description('Key Vault secret URI containing the GitHub App private key. Grant the Studio identity Key Vault Secrets User separately.')
param policyReviewGitHubPrivateKeySecretUri string = ''

@description('Entra principal object ID allowed to submit policy review pull requests through authenticated Studio ingress.')
param policyReviewAllowedPrincipal string = ''

@description('Microsoft Entra application client ID used by Container Apps built-in authentication.')
param studioAuthClientId string = ''

@description('Microsoft Entra tenant ID for Studio authentication.')
param studioAuthTenantId string = tenant().tenantId

@description('Key Vault secret URI containing the Studio authentication application client secret.')
param studioAuthClientSecretUri string = ''

@description('Existing Log Analytics workspace.')
param logAnalyticsWorkspaceName string = 'logs-xbk6ickycmp22'

@description('Existing Application Insights component.')
param applicationInsightsName string = 'appi-xbk6ickycmp22'

@description('Existing Azure App Configuration policy authority.')
param appConfigurationName string = 'appcs-xbk6ickycmp22'

var suffix = uniqueString(resourceGroup().id)
var containerAppName = 'ca-tokeneconomics-studio'
var environmentName = 'cae-tokeneconomics'
var identityName = 'id-tokeneconomics-studio'
var registryName = replace('crtokeneconomics${substring(suffix, 0, 8)}', '-', '')
var storageName = 'ststudio${substring(suffix, 0, 12)}'
var fileShareName = 'studio-state'
var storageMountName = 'studio-state'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

resource appConfiguration 'Microsoft.AppConfiguration/configurationStores@2023-03-01' existing = {
  name: appConfigurationName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: {
    workload: 'TokenEconomics'
    evidenceStatus: 'research-prototype'
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    policies: {
      quarantinePolicy: {
        status: 'disabled'
      }
      retentionPolicy: {
        days: 7
        status: 'disabled'
      }
      trustPolicy: {
        type: 'Notary'
        status: 'disabled'
      }
    }
  }
  tags: {
    workload: 'TokenEconomics'
    evidenceStatus: 'research-prototype'
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, 'AcrPull')
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource appConfigurationReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appConfiguration.id, identity.id, 'AppConfigurationDataReader')
  scope: appConfiguration
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '516239f1-63e1-4d78-a4de-a74fb236a071'
    )
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    allowBlobPublicAccess: false
    allowCrossTenantReplication: false
    allowSharedKeyAccess: true
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
  tags: {
    workload: 'TokenEconomics'
    purpose: 'Container Apps Azure Files mount'
    evidenceStatus: 'research-prototype'
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: fileShareName
  properties: {
    enabledProtocols: 'SMB'
    shareQuota: 5
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
  tags: {
    workload: 'TokenEconomics'
    evidenceStatus: 'research-prototype'
  }
}

resource environmentStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: environment
  name: storageMountName
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: fileShare.name
      accessMode: 'ReadWrite'
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: concat(
        empty(policyReviewGitHubPrivateKeySecretUri) ? [] : [
          {
          name: 'tokengov-github-app-private-key'
          keyVaultUrl: policyReviewGitHubPrivateKeySecretUri
          identity: identity.id
          }
        ],
        empty(studioAuthClientSecretUri) ? [] : [
          {
          name: 'tokengov-studio-auth-client-secret'
          keyVaultUrl: studioAuthClientSecretUri
          identity: identity.id
          }
        ]
      )
      ingress: {
        external: true
        allowInsecure: false
        targetPort: 8765
        transport: 'auto'
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'studio'
          image: containerImage
          env: concat([
            {
              name: 'TOKENECONOMICS_HOST'
              value: '0.0.0.0'
            }
            {
              name: 'TOKENECONOMICS_PORT'
              value: '8765'
            }
            {
              name: 'TOKENECONOMICS_STATE_ROOT'
              value: '/data'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: identity.properties.clientId
            }
            {
              name: 'AZURE_TOKEN_CREDENTIALS'
              value: 'prod'
            }
            {
              name: 'AZURE_APPCONFIG_ENDPOINT'
              value: appConfiguration.properties.endpoint
            }
            {
              name: 'TOKENGOV_POLICY_KEY'
              value: 'tokengov:policy'
            }
            {
              name: 'TOKENGOV_POLICY_LABEL'
              value: policyLabel
            }
            {
              name: 'TOKENGOV_POLICY_SOURCE'
              value: 'azure'
            }
            {
              name: 'TOKENGOV_APPROVAL_URL'
              value: policyApprovalUrl
            }
            {
              name: 'TOKENGOV_APPROVAL_ENVIRONMENT'
              value: policyApprovalEnvironment
            }
            {
              name: 'TOKENGOV_GITHUB_APP_ID'
              value: policyReviewGitHubAppId
            }
            {
              name: 'TOKENGOV_GITHUB_APP_INSTALLATION_ID'
              value: policyReviewGitHubInstallationId
            }
            {
              name: 'TOKENGOV_GITHUB_REPOSITORY'
              value: policyReviewGitHubRepository
            }
            {
              name: 'TOKENGOV_GITHUB_BASE_BRANCH'
              value: policyReviewGitHubBaseBranch
            }
            {
              name: 'TOKENGOV_REVIEW_ALLOWED_PRINCIPAL'
              value: policyReviewAllowedPrincipal
            }
            {
              name: 'TOKENGOV_REVIEW_AUTHENTICATED_INGRESS'
              value: !empty(studioAuthClientId) && !empty(studioAuthClientSecretUri) ? 'container_apps' : ''
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: applicationInsights.properties.ConnectionString
            }
          ], empty(policyReviewGitHubPrivateKeySecretUri) ? [] : [
            {
              name: 'TOKENGOV_GITHUB_APP_PRIVATE_KEY'
              secretRef: 'tokengov-github-app-private-key'
            }
          ])
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/livez'
                port: 8765
              }
              initialDelaySeconds: 1
              periodSeconds: 5
              failureThreshold: 30
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/livez'
                port: 8765
              }
              initialDelaySeconds: 10
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/readyz'
                port: 8765
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
            }
          ]
          volumeMounts: [
            {
              volumeName: 'studio-state'
              mountPath: '/data'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
      volumes: [
        {
          name: 'studio-state'
          storageType: 'AzureFile'
          storageName: environmentStorage.name
        }
      ]
    }
  }

  dependsOn: [
    acrPull
    appConfigurationReader
  ]
  tags: {
    workload: 'TokenEconomics'
    evidenceStatus: 'research-prototype'
  }
}

resource studioAuth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (!empty(studioAuthClientId) && !empty(studioAuthClientSecretUri) && !empty(policyReviewAllowedPrincipal)) {
  parent: containerApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      excludedPaths: [
        '/health'
        '/livez'
        '/readyz'
      ]
      redirectToProvider: 'azureActiveDirectory'
      unauthenticatedClientAction: 'RedirectToLoginPage'
    }
    httpSettings: {
      requireHttps: true
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: studioAuthClientId
          clientSecretSettingName: 'tokengov-studio-auth-client-secret'
          openIdIssuer: '${az.environment().authentication.loginEndpoint}${studioAuthTenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            studioAuthClientId
            'api://${studioAuthClientId}'
          ]
          defaultAuthorizationPolicy: {
            allowedPrincipals: {
              identities: [
                policyReviewAllowedPrincipal
              ]
            }
          }
        }
      }
    }
  }
}

output containerAppName string = containerApp.name
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerRegistryName string = registry.name
output containerRegistryLoginServer string = registry.properties.loginServer
output managedEnvironmentName string = environment.name
output managedIdentityName string = identity.name
output storageAccountName string = storage.name
output fileShareName string = fileShare.name
