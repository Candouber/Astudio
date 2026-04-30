import { NavLink } from 'react-router-dom'
import {
  Box,
  Building2,
  CalendarClock,
  Languages,
  LayoutDashboard,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Wrench,
} from 'lucide-react'
import { useConfigStore } from '../../stores/configStore'
import { useI18n } from '../../i18n/useI18n'
import './Sidebar.css'

const NAV_ITEMS = [
  { to: '/', icon: MessageSquare, labelKey: 'sidebar.nav.chat' as const },
  { to: '/tasks', icon: LayoutDashboard, labelKey: 'sidebar.nav.tasks' as const },
  { to: '/studios', icon: Building2, labelKey: 'sidebar.nav.studios' as const },
  { to: '/sandboxes', icon: Box, labelKey: 'sidebar.nav.sandboxes' as const },
  { to: '/schedules', icon: CalendarClock, labelKey: 'sidebar.nav.schedules' as const },
  { to: '/skills', icon: Wrench, labelKey: 'sidebar.nav.skills' as const },
]

interface SidebarProps {
  collapsed: boolean
  onToggleCollapsed: () => void
}

export default function Sidebar({ collapsed, onToggleCollapsed }: SidebarProps) {
  const openModal = useConfigStore(s => s.openModal)
  const { locale, toggleLocale, t } = useI18n()
  const CollapseIcon = collapsed ? PanelLeftOpen : PanelLeftClose
  const collapseHint = collapsed ? t('sidebar.expand') : t('sidebar.collapse')
  const langTitle = locale === 'zh' ? t('sidebar.switchToEn') : t('sidebar.switchToZh')

  return (
    <aside className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''}`}>
      <div className="sidebar__brand">
        <img className="sidebar__logo" src="/astudio-icon.png" alt="" />
        <span className="sidebar__name">AStudio</span>
        <button
          className="sidebar__collapse"
          type="button"
          onClick={onToggleCollapsed}
          aria-label={collapseHint}
          title={collapseHint}
        >
          <CollapseIcon size={16} />
        </button>
      </div>

      <nav className="sidebar__nav">
        {NAV_ITEMS.map(({ to, icon: Icon, labelKey }) => {
          const label = t(labelKey)
          return (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `sidebar__link ${isActive ? 'sidebar__link--active' : ''}`
              }
              title={collapsed ? label : undefined}
            >
              <Icon size={18} />
              <span className="sidebar__link-label">{label}</span>
            </NavLink>
          )
        })}
      </nav>

      <div className="sidebar__footer">
        <button
          type="button"
          className="sidebar__link sidebar__lang"
          onClick={toggleLocale}
          aria-label={langTitle}
          title={langTitle}
        >
          <Languages size={18} />
          <span className="sidebar__link-label sidebar__lang-label">
            <span className={locale === 'zh' ? 'sidebar__lang-active' : undefined}>中</span>
            <span className="sidebar__lang-sep">/</span>
            <span className={locale === 'en' ? 'sidebar__lang-active' : undefined}>EN</span>
          </span>
        </button>
        <button
          type="button"
          className="sidebar__link"
          onClick={openModal}
          title={collapsed ? t('sidebar.settings') : undefined}
        >
          <Settings size={18} />
          <span className="sidebar__link-label">{t('sidebar.settings')}</span>
        </button>
      </div>
    </aside>
  )
}
