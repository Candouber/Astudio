import { Check } from 'lucide-react'
import type { SkillPoolItem } from '../../types'
import { useI18n } from '../../i18n/useI18n'
import './SkillPicker.css'

interface Props {
  skills: SkillPoolItem[]
  selected: string[]
  onChange: (value: string[]) => void
  compact?: boolean
}

export default function SkillPicker({ skills, selected, onChange, compact = false }: Props) {
  const { t } = useI18n()
  const enabled = skills.filter(skill => skill.enabled)
  const enabledSlugs = new Set(enabled.map(skill => skill.slug))
  const bySlug = new Map(skills.map(skill => [skill.slug, skill]))
  const missing = selected
    .filter(slug => !enabledSlugs.has(slug))
    .map(slug => bySlug.get(slug) || {
      slug,
      name: slug,
      description: t('skillPickerUi.missingDesc'),
      category: t('skillPickerUi.missingCategory'),
      enabled: true,
      builtin: false,
      created_at: '',
      updated_at: '',
    })
  const options = [...enabled, ...missing]

  const toggle = (slug: string) => {
    onChange(selected.includes(slug)
      ? selected.filter(item => item !== slug)
      : [...selected, slug])
  }

  if (options.length === 0) {
    return <div className="common-skill-picker__empty">{t('skillPickerUi.empty')}</div>
  }

  return (
    <div className={`common-skill-picker ${compact ? 'common-skill-picker--compact' : ''}`}>
      {options.map(skill => {
        const active = selected.includes(skill.slug)
        return (
          <button
            key={skill.slug}
            type="button"
            className={`common-skill ${active ? 'common-skill--on' : ''}`}
            onClick={() => toggle(skill.slug)}
            title={skill.description || skill.name}
          >
            {active ? <Check size={12} /> : null}
            <span>{skill.slug}</span>
          </button>
        )
      })}
    </div>
  )
}
