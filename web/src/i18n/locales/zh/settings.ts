import type { TranslationTree } from '../../types'

const zh: TranslationTree = {
  settings: {
    reasoningEffort: {
      default: '默认',
      none: '关闭推理增强',
      minimal: '极低',
      low: '低',
      medium: '中',
      high: '高',
      xhigh: '极高',
    },
    thinkingType: {
      default: '默认',
      enabled: '开启',
      adaptive: '自适应',
    },
    providersIntro:
      'AStudio 支持使用 LiteLLM 接入多种模型。在此配置的 API 密钥及端点将保存在本地。',
    oauthAuthenticated: '已授权',
    oauthPending: '等待授权...',
    testing: '测试中...',
    testConnectivity: '测试连通性',
    deleteProvider: '删除供应商',
    oauthChatgpt:
      'OpenAI Subscription 使用 OAuth 授权登录 ChatGPT 订阅账号，无需 API Key。点击下方按钮将弹出浏览器完成授权。',
    oauthCopilot:
      'GitHub Copilot 使用 OAuth 授权。点击下方按钮，通过浏览器完成 GitHub 账号授权。',
    accountId: '账号 ID：',
    waitBrowserOAuth: '等待浏览器授权...',
    oauthLoginBrowser: '通过浏览器授权登录',
    oauthManualHint:
      '若自动跳转失败（如在远程环境中），请复制完成授权后的全路径 URL 并在此提交：',
    openAuthPage: '→ 点击此处手动访问授权页面',
    oauthCallbackPlaceholder: 'http://localhost:12345/callback?code=...',
    submit: '提交',
    revokeOAuth: '撤销授权 / 退出登录',
    availableModels: '可用模型：',
    litellmSlugLabel: 'LiteLLM 路由前缀（可选）',
    litellmSlugHelp:
      '必须与 LiteLLM 支持的标识一致（如 openai、anthropic）。自建 OpenAI 兼容网关时请填 openai（API Key/Base 仍为下方配置）；可与上方 Provider Name 不同。',
    litellmSlugPlaceholder: '留空则用 Provider Name',
    modelsListLabel: '模型列表',
    modelsListHelp:
      '每行一条本地模型名（如 gpt-4o、qwen-max）。路由会保存为 Provider Name/模型名。',
    modelAliasesLabel: '实际模型映射（可选）',
    modelAliasesHelp:
      '当本地模型名和实际调用模型不一致时填写。格式：本地模型名 = 实际模型名。会自动补上 LiteLLM 路由前缀。',
    displayNamesLabel: '模型显示名（可选）',
    displayNamesHelp:
      '只影响界面和任务记录显示，不影响实际调用。格式：本地模型名 = 显示名，也支持 =>。',
    apiKey: 'API Key',
    apiBase: 'API Base / Endpoint (可选)',
    addCustomProvider: '添加自定义供应商',
    routingIntro:
      '按角色分别指定执行模型与推理方式。这里会保留所有已配置模型，未连通的模型会单独标记，避免已保存的分路看起来像丢失。',
    routingEmpty:
      '当前还没有已连通的模型供应商。你仍然可以看到已保存的分路配置，但要让这些模型真正可用，还需要先在「模型供应商 API」里完成连接。',
    roleAgentZeroTitle: '0 号 Agent',
    roleAgentZeroSubtitle: '总控规划',
    roleAgentZeroHint:
      '负责理解用户目标、选择工作室路径并拆解任务，适合使用推理能力更强的模型。',
    roleSubAgentsTitle: '执行 Agent',
    roleSubAgentsSubtitle: '任务执行',
    roleSubAgentsHint:
      '负责检索、分析、调用工具和产出结果，通常使用响应更快、成本更低的执行模型。',
    roleDistillTitle: '上下文蒸馏',
    roleDistillSubtitle: '记忆压缩',
    roleDistillHint:
      '负责压缩历史上下文、生成经验摘要和用户事实，优先选择稳定且便宜的轻量模型。',
    execModel: '执行模型',
    selectModel: '选择模型…',
    disconnectedSuffix: '（未连通）',
    customModel: '✏️ {{model}} (自定义)',
    reasoningIntensity: '推理强度',
    thinkingMode: '思考模式',
    thinkingBudget: '思考预算（仅 Anthropic）',
    thinkingBudgetPlaceholder: '留空表示不单独指定',
    customWarn: '⚠️ 当前值为手动输入，未在已连通供应商中找到对应模型。',
    modalTitle: '系统配置',
    tabProviders: '模型供应商 API',
    tabRouting: '角色模型分路',
    saveConfig: '保存配置',
    savingConfig: '保存中...',
    providerNameLabel: 'Provider Name',
    providerNamePlaceholder: 'e.g. openai',
  },
}

export default zh
