import React, { useState, useEffect, useRef, useCallback } from 'react'
import { X, Settings, Database, Activity, CheckCircle, AlertTriangle, ChevronDown, Plus, Trash2, LogIn, LogOut, Loader2, ShieldCheck } from 'lucide-react'
import { useConfigStore } from '../../stores/configStore'
import type { AppConfig, LLMProvider, ReasoningEffort, RoleModelConfig, ThinkingType } from '../../types'
import { api } from '../../api/client'
import type { OAuthStatus, OAuthStatusResponse } from '../../api/client'
import { useI18n } from '../../i18n/useI18n'
import './SettingsModal.css'

const REASONING_EFFORT_VALUES: ReasoningEffort[] = ['default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh']
const THINKING_TYPE_VALUES: ThinkingType[] = ['default', 'disabled', 'enabled', 'adaptive']

function getErrorMessage(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : fallback
}

function toLocalModelId(provider: LLMProvider, value: string) {
  const model = value.trim()
  if (!model) return ''
  const prefix = `${provider.name}/`
  if (model.startsWith(prefix)) return model
  return `${provider.name}/${model}`
}

function modelAlias(provider: LLMProvider, model: string) {
  const prefix = `${provider.name}/`
  return model.startsWith(prefix) ? model.slice(prefix.length) : model
}

function displayModelName(provider: LLMProvider, model: string) {
  const configured = provider.model_display_names?.[model]?.trim()
  if (configured) return configured
  return modelAlias(provider, model)
}

function serializeProviderModels(provider: LLMProvider) {
  return provider.models.map(model => modelAlias(provider, model)).join('\n')
}

function serializeModelAliases(provider: LLMProvider) {
  return Object.entries(provider.model_aliases || {})
    .map(([alias, target]) => `${modelAlias(provider, alias)} = ${target}`)
    .join('\n')
}

function serializeModelDisplayNames(provider: LLMProvider) {
  return Object.entries(provider.model_display_names || {})
    .map(([model, display]) => `${modelAlias(provider, model)} = ${display}`)
    .join('\n')
}

function parseKeyValueLines(text: string) {
  const out: Record<string, string> = {}
  text.split(/\r?\n/).forEach(line => {
    const trimmed = line.trim()
    if (!trimmed) return
    const match = trimmed.match(/^(.+?)(?:\s*=>\s*|\s*=\s*)(.+)$/)
    if (!match) return
    const model = match[1].trim()
    const display = match[2].trim()
    if (model && display) out[model] = display
  })
  return out
}

function normalizeRoleModelConfig(value: unknown, fallbackModel: string): RoleModelConfig {
  if (typeof value === 'string') {
    return {
      model: value,
      reasoning_effort: 'default',
      thinking_type: 'default',
      thinking_budget_tokens: null,
    }
  }
  if (value && typeof value === 'object') {
    const record = value as Partial<RoleModelConfig>
    return {
      model: record.model || fallbackModel,
      reasoning_effort: record.reasoning_effort ?? 'default',
      thinking_type: record.thinking_type ?? 'default',
      thinking_budget_tokens: record.thinking_budget_tokens ?? null,
    }
  }
  return {
    model: fallbackModel,
    reasoning_effort: 'default',
    thinking_type: 'default',
    thinking_budget_tokens: null,
  }
}

function normalizeSettingsConfig(config: AppConfig): AppConfig {
  const cloned = JSON.parse(JSON.stringify(config)) as AppConfig
  const assignment = cloned.model_assignment as unknown as Record<string, unknown>
  cloned.model_assignment = {
    agent_zero: normalizeRoleModelConfig(assignment?.agent_zero, 'gpt-4o'),
    sub_agents: normalizeRoleModelConfig(assignment?.sub_agents, 'gpt-4o-mini'),
    distillation: normalizeRoleModelConfig(assignment?.distillation, 'gpt-4o-mini'),
  }
  cloned.llm_providers = cloned.llm_providers.map(provider => ({
    ...provider,
    model_aliases: provider.model_aliases || {},
    model_display_names: provider.model_display_names || {},
  }))
  return cloned
}

export default function SettingsModal() {
  const { config, isSettingsModalOpen, closeModal, fetchConfig, updateConfig, isSaving } = useConfigStore()
  const { t } = useI18n()
  const [activeTab, setActiveTab] = useState<'providers' | 'routing'>('providers')
  const [localConfig, setLocalConfig] = useState<AppConfig | null>(null)
  const [expandedProvider, setExpandedProvider] = useState<number | null>(null)

  const [testStates, setTestStates] = useState<Record<string, { loading: boolean; success?: boolean; msg?: string }>>({})

  const [oauthStates, setOauthStates] = useState<Record<string, OAuthStatusResponse>>({})
  const [oauthInitiating, setOauthInitiating] = useState<Record<string, boolean>>({})
  const [oauthCallbackUrls, setOauthCallbackUrls] = useState<Record<string, string>>({})
  const pollTimersRef = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  useEffect(() => {
    if (isSettingsModalOpen && config) {
      setLocalConfig(normalizeSettingsConfig(config))
    }
  }, [config, isSettingsModalOpen])

  useEffect(() => {
    if (isSettingsModalOpen) {
      void fetchConfig()
    }
  }, [fetchConfig, isSettingsModalOpen])

  const startOAuthPolling = useCallback((providerName: string) => {
    if (pollTimersRef.current[providerName]) {
      clearInterval(pollTimersRef.current[providerName])
    }
    pollTimersRef.current[providerName] = setInterval(async () => {
      try {
        const status = await api.getOAuthStatus(providerName)
        setOauthStates(prev => ({ ...prev, [providerName]: status }))
        if (status.status === 'authenticated' || status.status === 'failed') {
          clearInterval(pollTimersRef.current[providerName])
          delete pollTimersRef.current[providerName]
        }
      } catch {
        return
      }
    }, 2000)
  }, [])

  useEffect(() => {
    if (!isSettingsModalOpen || !localConfig) return
    const oauthProviders = localConfig.llm_providers.filter(p => p.is_oauth)
    oauthProviders.forEach(async (p) => {
      try {
        const status = await api.getOAuthStatus(p.name)
        setOauthStates(prev => ({ ...prev, [p.name]: status }))
      } catch { /* ignore */ }
    })
  }, [isSettingsModalOpen, localConfig])

  useEffect(() => {
    const pollTimers = pollTimersRef.current
    return () => {
      Object.values(pollTimers).forEach(t => clearInterval(t))
    }
  }, [])

  if (!isSettingsModalOpen || !localConfig) return null

  const handleProviderChange = (index: number, field: keyof LLMProvider, value: string) => {
    const updated = [...localConfig.llm_providers]
    updated[index] = { ...updated[index], [field]: value }
    setLocalConfig({ ...localConfig, llm_providers: updated })
  }

  const handleModelsTextChange = (index: number, text: string) => {
    const provider = localConfig.llm_providers[index]
    const models = text.split(/[\n,]+/)
      .map((s) => toLocalModelId(provider, s))
      .filter(Boolean)
    const updated = [...localConfig.llm_providers]
    updated[index] = { ...updated[index], models }
    setLocalConfig({ ...localConfig, llm_providers: updated })
  }

  const handleModelAliasesChange = (index: number, text: string) => {
    const provider = localConfig.llm_providers[index]
    const parsed = parseKeyValueLines(text)
    const model_aliases: Record<string, string> = {}
    const models = new Set(provider.models)
    Object.entries(parsed).forEach(([alias, target]) => {
      const localId = toLocalModelId(provider, alias)
      if (!localId || !target) return
      model_aliases[localId] = target
      models.add(localId)
    })
    const updated = [...localConfig.llm_providers]
    updated[index] = { ...updated[index], model_aliases, models: Array.from(models) }
    setLocalConfig({ ...localConfig, llm_providers: updated })
  }

  const handleModelDisplayNamesChange = (index: number, text: string) => {
    const provider = localConfig.llm_providers[index]
    const parsed = parseKeyValueLines(text)
    const model_display_names: Record<string, string> = {}
    Object.entries(parsed).forEach(([alias, display]) => {
      const localId = toLocalModelId(provider, alias)
      if (localId && display) model_display_names[localId] = display
    })
    const updated = [...localConfig.llm_providers]
    updated[index] = { ...updated[index], model_display_names }
    setLocalConfig({ ...localConfig, llm_providers: updated })
  }

  const handleAddProvider = () => {
    const newProvider: LLMProvider = {
      name: `custom_provider_${localConfig.llm_providers.length + 1}`,
      api_key: '',
      endpoint: '',
      models: [],
      model_aliases: {},
      model_display_names: {},
      litellm_provider: '',
    }
    setLocalConfig({ ...localConfig, llm_providers: [...localConfig.llm_providers, newProvider] })
    setExpandedProvider(localConfig.llm_providers.length)
  }

  const handleDeleteProvider = (index: number) => {
    const updated = localConfig.llm_providers.filter((_, i) => i !== index)
    setLocalConfig({ ...localConfig, llm_providers: updated })
    if (expandedProvider === index) setExpandedProvider(null)
  }

  const handleInitiateOAuth = async (e: React.MouseEvent, providerName: string) => {
    e.stopPropagation()
    setOauthInitiating(prev => ({ ...prev, [providerName]: true }))
    try {
      await api.initiateOAuth(providerName)
      setOauthStates(prev => ({
        ...prev,
        [providerName]: { status: 'pending', account_id: null, error: null }
      }))
      startOAuthPolling(providerName)
    } catch (err: unknown) {
      setOauthStates(prev => ({
        ...prev,
        [providerName]: { status: 'failed', account_id: null, error: getErrorMessage(err, 'Failed to start OAuth') }
      }))
    } finally {
      setOauthInitiating(prev => ({ ...prev, [providerName]: false }))
    }
  }

  const handleSubmitCallback = async (e: React.MouseEvent, providerName: string) => {
    e.stopPropagation()
    const url = oauthCallbackUrls[providerName]
    if (!url) return
    try {
      await api.submitOAuthCallback(providerName, url)
      setOauthCallbackUrls(prev => ({ ...prev, [providerName]: '' }))
    } catch (err: unknown) {
      console.error('Submit callback failed:', err)
      setOauthStates(prev => ({
        ...prev,
        [providerName]: { status: 'failed', account_id: null, error: getErrorMessage(err, 'Failed to submit callback') }
      }))
    }
  }

  const handleRevokeOAuth = async (e: React.MouseEvent, providerName: string) => {
    e.stopPropagation()
    try {
      await api.revokeOAuth(providerName)
      setOauthStates(prev => ({
        ...prev,
        [providerName]: { status: 'not_started', account_id: null, error: null }
      }))
    } catch (err: unknown) {
      console.error('Revoke failed:', err)
    }
  }



  const handleTestProvider = async (e: React.MouseEvent, provider: LLMProvider) => {
    e.stopPropagation()
    setTestStates(prev => ({ ...prev, [provider.name]: { loading: true } }))
    try {
      const testModel =
        provider.models.length > 0 ? provider.models[0] : 'gpt-4o'
      const res = await api.testProvider({
        name: provider.name,
        api_key: provider.api_key,
        endpoint: provider.endpoint,
        test_model: testModel,
        litellm_provider: provider.litellm_provider?.trim()
          ? provider.litellm_provider.trim()
          : null,
        model_aliases: provider.model_aliases || {},
      })

      setTestStates(prev => ({
        ...prev,
        [provider.name]: { loading: false, success: res.success, msg: res.message }
      }))
    } catch (e: unknown) {
      setTestStates(prev => ({
        ...prev,
        [provider.name]: { loading: false, success: false, msg: getErrorMessage(e, 'Network error') }
      }))
    }
  }

  const toggleExpand = (index: number) => {
    setExpandedProvider(prev => prev === index ? null : index)
  }

  const handleRoutingChange = (
    field: 'agent_zero' | 'sub_agents' | 'distillation',
    key: keyof RoleModelConfig,
    value: string | number | null,
  ) => {
    setLocalConfig({
      ...localConfig,
      model_assignment: {
        ...localConfig.model_assignment,
        [field]: {
          ...localConfig.model_assignment[field],
          [key]: value,
        },
      }
    })
  }

  const handleSave = async () => {
    await updateConfig(localConfig)
    closeModal()
  }

  const renderProviders = () => (
    <div className="providers-list">
      <p className="text-muted text-sm mb-4">
        {t('settings.providersIntro')}
      </p>

      {localConfig.llm_providers.map((provider, i) => {
        const testState = testStates[provider.name]
        const isExpanded = expandedProvider === i
        const isDefault = ['openai', 'anthropic', 'gemini', 'deepseek', 'groq', 'zhipu', 'ollama', 'chatgpt', 'openai_codex', 'github_copilot'].includes(provider.name)
        const oauthState = oauthStates[provider.name]
        const oauthStatus: OAuthStatus = oauthState?.status ?? 'not_started'
        const isAuthenticated = oauthStatus === 'authenticated'
        const isPending = oauthStatus === 'pending'
        const isInitiating = oauthInitiating[provider.name]

        return (
          <div key={i} className={`provider-card ${isExpanded ? 'expanded' : ''} ${provider.is_oauth ? 'oauth-card' : ''}`}>
            <div className="provider-card__header" onClick={() => toggleExpand(i)}>
              <div className="provider-card__title-group">
                <ChevronDown size={18} className="provider-card__icon" />
                {provider.is_oauth && <ShieldCheck size={15} className="oauth-badge-icon" />}
                <span className="provider-card__title">
                  {provider.name === 'chatgpt' || provider.name === 'openai_codex' ? 'OpenAI Subscription'
                    : provider.name === 'github_copilot' ? 'GitHub Copilot'
                    : provider.name}
                </span>
                {!provider.is_oauth && provider.api_key && <CheckCircle size={14} color="var(--accent-success)" />}
                {provider.is_oauth && isAuthenticated && (
                  <span className="oauth-authenticated-badge"><CheckCircle size={13} /> {t('settings.oauthAuthenticated')}</span>
                )}
                {provider.is_oauth && isPending && (
                  <span className="oauth-pending-badge"><Loader2 size={13} className="spin" /> {t('settings.oauthPending')}</span>
                )}
              </div>

              <div className="provider-card__actions" onClick={e => e.stopPropagation()}>
                {!provider.is_oauth && (
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={(e) => handleTestProvider(e, provider)}
                    disabled={testState?.loading || !provider.api_key}
                    style={{ fontSize: '0.8rem', padding: '4px 10px' }}
                  >
                    {testState?.loading ? t('settings.testing') : t('settings.testConnectivity')}
                  </button>
                )}
                {!isDefault && (
                  <button type="button" className="btn-delete" onClick={() => handleDeleteProvider(i)} title={t('settings.deleteProvider')}>
                    <Trash2 size={16} />
                  </button>
                )}
              </div>
            </div>

            {isExpanded && (
              <div className="provider-card__content">
                {!isDefault && (
                  <div className="setting-group" style={{ marginBottom: '16px' }}>
                    <label>{t('settings.providerNameLabel')}</label>
                    <input
                      type="text"
                      className="input-base"
                      placeholder={t('settings.providerNamePlaceholder')}
                      value={provider.name}
                      onChange={e => handleProviderChange(i, 'name', e.target.value)}
                    />
                  </div>
                )}

                {provider.is_oauth ? (
                  <div className="oauth-section">
                    <p className="oauth-description">
                      {provider.name === 'chatgpt' || provider.name === 'openai_codex'
                        ? t('settings.oauthChatgpt')
                        : t('settings.oauthCopilot')}
                    </p>

                    {oauthState?.error && (
                      <div className="test-badge error mb-4">
                        <AlertTriangle size={14} />
                        <span>{oauthState.error}</span>
                      </div>
                    )}

                    {isAuthenticated && oauthState?.account_id && (
                      <div className="oauth-account-info">
                        <ShieldCheck size={14} />
                        <span>{t('settings.accountId')}{oauthState.account_id}</span>
                      </div>
                    )}

                    <div className="oauth-actions" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                      {!isAuthenticated ? (
                        <>
                          <button
                            className="btn-oauth-login"
                            onClick={(e) => handleInitiateOAuth(e, provider.name)}
                            disabled={isPending || isInitiating}
                          >
                            {isPending || isInitiating
                              ? <><Loader2 size={16} className="spin" /> {t('settings.waitBrowserOAuth')}</>
                              : <><LogIn size={16} /> {t('settings.oauthLoginBrowser')}</>
                            }
                          </button>

                          {isPending && (
                            <div className="oauth-manual-fallback" style={{ marginTop: '8px', padding: '12px', background: 'var(--surface-sunken)', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                              <p className="text-muted text-sm mb-2" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                <AlertTriangle size={14} color="var(--accent-warning)" />
                                {t('settings.oauthManualHint')}
                              </p>
                              {oauthState?.auth_url && (
                                <p className="text-xs mb-2" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                  <a href={oauthState.auth_url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)', textDecoration: 'underline' }}>
                                    {t('settings.openAuthPage')}
                                  </a>
                                </p>
                              )}
                              <div style={{ display: 'flex', gap: '8px' }}>
                                <input
                                  type="text"
                                  className="input-base"
                                  placeholder={t('settings.oauthCallbackPlaceholder')}
                                  value={oauthCallbackUrls[provider.name] || ''}
                                  onChange={e => setOauthCallbackUrls(prev => ({ ...prev, [provider.name]: e.target.value }))}
                                  onClick={e => e.stopPropagation()}
                                  style={{ flex: 1, fontSize: '13px' }}
                                />
                                <button
                                  className="btn btn-secondary btn-sm"
                                  onClick={(e) => handleSubmitCallback(e, provider.name)}
                                  disabled={!oauthCallbackUrls[provider.name]}
                                >
                                  {t('settings.submit')}
                                </button>
                              </div>
                            </div>
                          )}
                        </>
                      ) : (
                        <button
                          className="btn-oauth-revoke"
                          onClick={(e) => handleRevokeOAuth(e, provider.name)}
                        >
                          <LogOut size={16} /> {t('settings.revokeOAuth')}
                        </button>
                      )}
                    </div>

                    <div className="oauth-model-hint">
                      <span>{t('settings.availableModels')}</span>
                      {provider.models.map(m => <code key={m}>{m}</code>)}
                    </div>
                  </div>
                ) : (
                  <>
                    {testState && testState.msg && (
                       <div className={`test-badge ${testState.success ? 'success' : 'error'} mb-4`}>
                         {testState.success ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}
                         <span>{testState.msg}</span>
                       </div>
                    )}

                    <div className="setting-group" style={{ marginBottom: '16px' }}>
                      <label>{t('settings.litellmSlugLabel')}</label>
                      <p className="text-muted text-xs mb-2">
                        {t('settings.litellmSlugHelp')}
                      </p>
                      <input
                        type="text"
                        className="input-base"
                        placeholder={t('settings.litellmSlugPlaceholder')}
                        value={provider.litellm_provider ?? ''}
                        onChange={(e) => handleProviderChange(i, 'litellm_provider', e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </div>

                    <div className="setting-group" style={{ marginBottom: '16px' }}>
                      <label>{t('settings.modelsListLabel')}</label>
                      <p className="text-muted text-xs mb-2">
                        {t('settings.modelsListHelp')}
                      </p>
                      <textarea
                        className="input-base"
                        rows={5}
                        style={{
                          resize: 'vertical',
                          fontFamily: 'var(--font-mono, monospace)',
                          fontSize: '13px',
                        }}
                        placeholder="gpt-4o-mini"
                        value={serializeProviderModels(provider)}
                        onChange={(e) => handleModelsTextChange(i, e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </div>

                    <div className="setting-group" style={{ marginBottom: '16px' }}>
                      <label>{t('settings.modelAliasesLabel')}</label>
                      <p className="text-muted text-xs mb-2">
                        {t('settings.modelAliasesHelp')}
                      </p>
                      <textarea
                        className="input-base"
                        rows={3}
                        style={{
                          resize: 'vertical',
                          fontFamily: 'var(--font-mono, monospace)',
                          fontSize: '13px',
                        }}
                        placeholder="qwen-max = Qwen/Qwen3-235B-A22B-Instruct-2507"
                        value={serializeModelAliases(provider)}
                        onChange={(e) => handleModelAliasesChange(i, e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </div>

                    <div className="setting-group" style={{ marginBottom: '16px' }}>
                      <label>{t('settings.displayNamesLabel')}</label>
                      <p className="text-muted text-xs mb-2">
                        {t('settings.displayNamesHelp')}
                      </p>
                      <textarea
                        className="input-base"
                        rows={3}
                        style={{
                          resize: 'vertical',
                          fontFamily: 'var(--font-mono, monospace)',
                          fontSize: '13px',
                        }}
                        placeholder="qwen-max = Qwen Max"
                        value={serializeModelDisplayNames(provider)}
                        onChange={(e) => handleModelDisplayNamesChange(i, e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </div>

                    <div className="setting-group" style={{ marginBottom: '16px' }}>
                      <label>{t('settings.apiKey')}</label>
                      <input
                        type="password"
                        className="input-base"
                        placeholder="sk-..."
                        value={provider.api_key || ''}
                        onChange={e => handleProviderChange(i, 'api_key', e.target.value)}
                      />
                    </div>

                    <div className="setting-group" style={{ marginBottom: 0 }}>
                      <label>{t('settings.apiBase')}</label>
                      <input
                        type="text"
                        className="input-base"
                        placeholder="e.g. https://api.openai.com/v1"
                        value={provider.endpoint || ''}
                        onChange={e => handleProviderChange(i, 'endpoint', e.target.value)}
                      />
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        )
      })}

      <button type="button" className="add-provider-btn" onClick={handleAddProvider}>
        <Plus size={18} /> {t('settings.addCustomProvider')}
      </button>
    </div>
  )

  const renderRouting = () => {
    const availableModels: {
      value: string;
      label: string;
      provider: string;
      isOauth: boolean;
      connected: boolean;
    }[] = []
    localConfig.llm_providers.forEach(provider => {
      const hasKey = !provider.is_oauth && !!provider.api_key
      const oauthOk = provider.is_oauth && oauthStates[provider.name]?.status === 'authenticated'
      const connected = Boolean(hasKey || oauthOk)
      provider.models.forEach(model => {
        availableModels.push({
          value: model,
          label: displayModelName(provider, model),
          provider: provider.name,
          isOauth: provider.is_oauth ?? false,
          connected,
        })
      })
    })
    const connectedModelCount = availableModels.filter(model => model.connected).length

    const roleCards: {
      key: 'agent_zero' | 'sub_agents' | 'distillation'
      icon: React.ReactNode
      titleKey: string
      subtitleKey: string
      hintKey: string
      color: string
    }[] = [
      {
        key: 'agent_zero',
        icon: <Database size={18} />,
        titleKey: 'settings.roleAgentZeroTitle',
        subtitleKey: 'settings.roleAgentZeroSubtitle',
        hintKey: 'settings.roleAgentZeroHint',
        color: '#818cf8',
      },
      {
        key: 'sub_agents',
        icon: <Activity size={18} />,
        titleKey: 'settings.roleSubAgentsTitle',
        subtitleKey: 'settings.roleSubAgentsSubtitle',
        hintKey: 'settings.roleSubAgentsHint',
        color: '#34d399',
      },
      {
        key: 'distillation',
        icon: <Database size={18} />,
        titleKey: 'settings.roleDistillTitle',
        subtitleKey: 'settings.roleDistillSubtitle',
        hintKey: 'settings.roleDistillHint',
        color: '#fb923c',
      },
    ]

    return (
      <div className="routing-settings">
        <p className="text-muted text-sm mb-4">
          {t('settings.routingIntro')}
        </p>
        {connectedModelCount === 0 && (
          <div className="routing-empty-hint">
            <AlertTriangle size={16} />
            <span>{t('settings.routingEmpty')}</span>
          </div>
        )}
        <div className="routing-cards">
          {roleCards.map(({ key, icon, titleKey, subtitleKey, hintKey, color }) => {
            const current = localConfig.model_assignment[key]
            const currentModel = current.model || ''
            const isCustom = currentModel && !availableModels.find(m => m.value === currentModel)
            return (
              <div key={key} className="routing-role-card" style={{ '--role-color': color } as React.CSSProperties}>
                <div className="routing-role-card__header">
                  <div className="routing-role-card__icon" style={{ color }}>{icon}</div>
                  <div className="routing-role-card__titles">
                    <span className="routing-role-card__title">{t(titleKey)}</span>
                    <span className="routing-role-card__subtitle">{t(subtitleKey)}</span>
                  </div>
                  {currentModel && (
                    <div className="routing-role-card__current">
                      <CheckCircle size={13} color={color} />
                      <code>{availableModels.find(m => m.value === currentModel)?.label || currentModel}</code>
                    </div>
                  )}
                </div>
                <p className="routing-role-card__hint">{t(hintKey)}</p>
                <div className="routing-role-card__form">
                  <div className="routing-role-card__field routing-role-card__field--full">
                    <label className="routing-role-card__label">{t('settings.execModel')}</label>
                    <div className="routing-role-card__select-wrap">
                      <select
                        className="routing-select"
                        value={isCustom ? '__custom__' : (currentModel || '')}
                        onChange={e => {
                          if (e.target.value !== '__custom__') {
                            handleRoutingChange(key, 'model', e.target.value)
                          }
                        }}
                      >
                        <option value="" disabled>{t('settings.selectModel')}</option>
                        {availableModels.map(m => (
                          <option key={m.value} value={m.value}>
                            {m.isOauth ? '🔐 ' : ''}{m.label}{m.connected ? '' : t('settings.disconnectedSuffix')}
                          </option>
                        ))}
                        {isCustom && (
                          <option value="__custom__">{t('settings.customModel', { model: currentModel })}</option>
                        )}
                      </select>
                    </div>
                  </div>
                  <div className="routing-role-card__field">
                    <label className="routing-role-card__label">{t('settings.reasoningIntensity')}</label>
                    <select
                      className="routing-select"
                      value={current.reasoning_effort ?? 'default'}
                      onChange={e => handleRoutingChange(key, 'reasoning_effort', e.target.value)}
                    >
                      {REASONING_EFFORT_VALUES.map(value => (
                        <option key={value} value={value}>{t(`settings.reasoningEffort.${value}`)}</option>
                      ))}
                    </select>
                  </div>
                  <div className="routing-role-card__field">
                    <label className="routing-role-card__label">{t('settings.thinkingMode')}</label>
                    <select
                      className="routing-select"
                      value={current.thinking_type ?? 'default'}
                      onChange={e => handleRoutingChange(key, 'thinking_type', e.target.value)}
                    >
                      {THINKING_TYPE_VALUES.map(value => (
                        <option key={value} value={value}>{t(`settings.thinkingType.${value}`)}</option>
                      ))}
                    </select>
                  </div>
                  <div className="routing-role-card__field routing-role-card__field--full">
                    <label className="routing-role-card__label">{t('settings.thinkingBudget')}</label>
                    <input
                      type="number"
                      min="0"
                      step="1"
                      className="input-base"
                      placeholder={t('settings.thinkingBudgetPlaceholder')}
                      value={current.thinking_budget_tokens ?? ''}
                      onChange={e => handleRoutingChange(
                        key,
                        'thinking_budget_tokens',
                        e.target.value === '' ? null : Number(e.target.value),
                      )}
                    />
                  </div>
                </div>
                {isCustom && (
                  <p className="text-xs mt-2" style={{ color: '#f59e0b' }}>
                    {t('settings.customWarn')}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div className="settings-modal-overlay" onClick={closeModal}>
      <div className="settings-modal" onClick={e => e.stopPropagation()}>
        <div className="settings-modal__header">
          <h2><Settings size={20} /> {t('settings.modalTitle')}</h2>
          <button type="button" className="btn btn-icon" onClick={closeModal}>
            <X size={20} />
          </button>
        </div>

        <div className="settings-modal__tabs">
          <div
            className={`settings-tab ${activeTab === 'providers' ? 'active' : ''}`}
            onClick={() => setActiveTab('providers')}
          >
            {t('settings.tabProviders')}
          </div>
          <div
            className={`settings-tab ${activeTab === 'routing' ? 'active' : ''}`}
            onClick={() => setActiveTab('routing')}
          >
            {t('settings.tabRouting')}
          </div>
        </div>

        <div className="settings-modal__content">
          {activeTab === 'providers' ? renderProviders() : renderRouting()}
        </div>

        <div className="settings-modal__footer">
          <button type="button" className="btn btn-secondary" onClick={closeModal}>{t('common.cancel')}</button>
          <button type="button" className="btn btn-primary" onClick={handleSave} disabled={isSaving}>
            {isSaving ? t('settings.savingConfig') : t('settings.saveConfig')}
          </button>
        </div>
      </div>
    </div>
  )
}
