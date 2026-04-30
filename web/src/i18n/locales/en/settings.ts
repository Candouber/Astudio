import type { TranslationTree } from '../../types'

const en: TranslationTree = {
  settings: {
    reasoningEffort: {
      default: 'Default',
      none: 'No reasoning boost',
      minimal: 'Minimal',
      low: 'Low',
      medium: 'Medium',
      high: 'High',
      xhigh: 'Extra high',
    },
    thinkingType: {
      default: 'Default',
      enabled: 'Enabled',
      adaptive: 'Adaptive',
    },
    providersIntro:
      'AStudio uses LiteLLM to reach many models. Keys and endpoints stay on this machine.',
    oauthAuthenticated: 'Signed in',
    oauthPending: 'Waiting for auth…',
    testing: 'Testing…',
    testConnectivity: 'Test connection',
    deleteProvider: 'Remove provider',
    oauthChatgpt:
      'OpenAI Subscription uses OAuth — no API key. Click below to authorize in the browser.',
    oauthCopilot: 'GitHub Copilot uses OAuth — authorize in the browser.',
    accountId: 'Account ID: ',
    waitBrowserOAuth: 'Waiting for browser…',
    oauthLoginBrowser: 'Sign in via browser',
    oauthManualHint:
      'If redirect fails (e.g. remote env), paste the full callback URL here:',
    openAuthPage: '→ Open authorization page',
    oauthCallbackPlaceholder: 'http://localhost:12345/callback?code=...',
    submit: 'Submit',
    revokeOAuth: 'Revoke / sign out',
    availableModels: 'Models: ',
    litellmSlugLabel: 'LiteLLM provider slug (optional)',
    litellmSlugHelp:
      'Must match LiteLLM (e.g. openai, anthropic). For OpenAI-compatible gateways use openai.',
    litellmSlugPlaceholder: 'Empty = provider name',
    modelsListLabel: 'Models',
    modelsListHelp:
      'One local model name per line, e.g. gpt-4o or qwen-max. Routes are saved as Provider Name/model.',
    modelAliasesLabel: 'Actual model mapping (optional)',
    modelAliasesHelp:
      'Use when the local model name differs from the actual model name. Format: local name = actual model name; the LiteLLM provider prefix is added automatically.',
    displayNamesLabel: 'Display names (optional)',
    displayNamesHelp:
      'UI-only mapping: local name = display name (also supports =>).',
    apiKey: 'API Key',
    apiBase: 'API Base / Endpoint (optional)',
    addCustomProvider: 'Add custom provider',
    routingIntro:
      'Pick models and reasoning per role. Disconnected models stay visible but marked.',
    routingEmpty:
      'No connected providers yet — finish API setup first to run saved routes.',
    roleAgentZeroTitle: 'Agent Zero',
    roleAgentZeroSubtitle: 'Orchestration',
    roleAgentZeroHint: 'Understands goals, picks studios, breaks down work — prefer stronger reasoning.',
    roleSubAgentsTitle: 'Worker agents',
    roleSubAgentsSubtitle: 'Execution',
    roleSubAgentsHint: 'Retrieval, tools, outputs — prefer fast, cost-effective models.',
    roleDistillTitle: 'Distillation',
    roleDistillSubtitle: 'Memory compression',
    roleDistillHint: 'Compress history and facts — prefer stable, cheap models.',
    execModel: 'Model',
    selectModel: 'Choose model…',
    disconnectedSuffix: ' (offline)',
    customModel: '✏️ {{model}} (custom)',
    reasoningIntensity: 'Reasoning effort',
    thinkingMode: 'Thinking mode',
    thinkingBudget: 'Thinking budget (Anthropic)',
    thinkingBudgetPlaceholder: 'Empty = use default',
    customWarn: '⚠️ Manual value — not found among connected models.',
    modalTitle: 'Settings',
    tabProviders: 'Model providers',
    tabRouting: 'Role routing',
    saveConfig: 'Save',
    savingConfig: 'Saving…',
    providerNameLabel: 'Provider name',
    providerNamePlaceholder: 'e.g. openai',
  },
}

export default en
