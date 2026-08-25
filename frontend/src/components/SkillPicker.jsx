import { useState } from 'react';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import { Label } from './ui/label';
import { cn } from '../lib/utils';

function formatChars(chars) {
  const value = Number(chars);
  if (!Number.isFinite(value) || value <= 0) return '0 chars';
  if (value < 1000) return `${value} chars`;
  return `${(value / 1000).toFixed(1)}k chars`;
}

function referenceLabel(count) {
  return count === 1 ? '1 reference' : `${count} references`;
}

/**
 * Composer multi-select for Agent Skills.
 * Purely presentational: the draft selection is owned by the caller.
 */
export default function SkillPicker({
  skills,
  selectedIds,
  defaultSkillIds,
  onChange,
  onResetToDefault,
  maxSelected,
  autoSelectEnabled = false,
  disabled = false,
  onManage,
}) {
  const [isExpanded, setIsExpanded] = useState(false);

  const availableSkills = Array.isArray(skills) ? skills : [];
  const selected = Array.isArray(selectedIds) ? selectedIds : [];
  const defaults = new Set(
    Array.isArray(defaultSkillIds) ? defaultSkillIds : [],
  );
  const hasOverrides = [...defaults].some((id) => !selected.includes(id));

  if (availableSkills.length === 0 && selected.length === 0) {
    return null;
  }

  const cap = Number(maxSelected) > 0 ? Number(maxSelected) : null;
  const atCap = cap !== null && selected.length >= cap;
  const triggerLabel = selected.length === 0
    ? (autoSelectEnabled ? 'Skills: auto' : 'Skills: none')
    : `Skills: ${selected.length} selected`;

  const toggleSkill = (skillId, checked) => {
    if (disabled) return;
    if (checked) {
      if (selected.includes(skillId) || atCap) return;
      onChange([...selected, skillId]);
    } else {
      onChange(selected.filter((id) => id !== skillId));
    }
  };

  return (
    <div className="skill-picker flex flex-col gap-2">
      <div className="skill-picker-bar flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="skill-picker-trigger h-8 gap-1.5 px-2.5 text-xs"
          onClick={() => setIsExpanded((current) => !current)}
          aria-expanded={isExpanded}
          aria-controls="skill-picker-list"
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            className="h-3.5 w-3.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
          </svg>
          <span className="skill-picker-trigger-label">{triggerLabel}</span>
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            className={cn('h-3.5 w-3.5 transition-transform', isExpanded && 'rotate-180')}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </Button>
        {onManage && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="skill-picker-manage-btn h-8 px-2.5 text-xs"
            onClick={() => onManage()}
          >
            Manage skills…
          </Button>
        )}
      </div>

      {isExpanded && (
        <div id="skill-picker-list" className="skill-picker-list flex flex-col gap-2">
          {availableSkills.length === 0 ? (
            <p className="skill-picker-empty rounded-lg border border-dashed border-border/60 bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
              No skills installed yet. Upload one from “Manage skills…”.
            </p>
          ) : (
            <div className="skill-picker-options grid gap-2">
              {availableSkills.map((skill) => {
                const isChecked = selected.includes(skill.id);
                const isDefault = defaults.has(skill.id);
                const isSkillDisabled = skill.enabled === false;
                const blockedByCap = !isChecked && atCap;
                const rowDisabled = disabled
                  || (!isChecked && (isSkillDisabled || !isDefault || blockedByCap));
                const optionId = `skill-option-${skill.id}`;
                const referenceCount = Number(skill.reference_count) || 0;
                return (
                  <div
                    key={skill.id}
                    className={cn(
                      'skill-picker-option flex items-start gap-3 rounded-lg border border-border/60 bg-background/80 p-3',
                      isChecked && 'border-primary/50 bg-primary/5',
                      rowDisabled && 'opacity-60'
                    )}
                  >
                    <Checkbox
                      id={optionId}
                      checked={isChecked}
                      disabled={rowDisabled}
                      onCheckedChange={(checked) => toggleSkill(skill.id, checked === true)}
                    />
                    <Label htmlFor={optionId} className="flex-1 cursor-pointer">
                      <div className="skill-picker-option-head flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium text-foreground">
                          {skill.name || skill.id}
                        </span>
                        {skill.version ? (
                          <Badge variant="outline" className="text-[10px]">
                            v{skill.version}
                          </Badge>
                        ) : null}
                        {isSkillDisabled && (
                          <Badge variant="subtle" className="text-[10px]">
                            Disabled
                          </Badge>
                        )}
                        {isDefault && !isChecked && (
                          <Badge variant="subtle" className="text-[10px]">
                            off for this chat
                          </Badge>
                        )}
                        {!isDefault && (
                          <Badge variant="outline" className="text-[10px]">
                            not in default
                          </Badge>
                        )}
                      </div>
                      {skill.description && (
                        <div className="skill-picker-option-description text-xs text-muted-foreground">
                          {skill.description}
                        </div>
                      )}
                      <div className="skill-picker-option-meta mt-1 text-[11px] text-muted-foreground">
                        {referenceLabel(referenceCount)} · {formatChars(skill.skill_md_chars)}
                      </div>
                    </Label>
                  </div>
                );
              })}
            </div>
          )}

          {atCap && (
            <p className="skill-picker-cap rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-900/90 dark:text-amber-100">
              Limit reached: up to {cap} skills per message. Uncheck one to pick another.
            </p>
          )}

          {hasOverrides && onResetToDefault && (
            <div className="skill-picker-overrides flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2">
              <span className="text-xs text-amber-900/90 dark:text-amber-100">
                Customized for this chat — some default skills are turned off.
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="skill-picker-reset-btn h-7 px-2 text-xs"
                onClick={() => onResetToDefault()}
                disabled={disabled}
              >
                Reset to default
              </Button>
            </div>
          )}

          {autoSelectEnabled && selected.length === 0 && (
            <p className="skill-picker-auto rounded-md border border-primary/40 bg-primary/5 px-3 py-2 text-xs text-muted-foreground">
              No skills selected — the backend will automatically pick skills whose
              description matches this question.
            </p>
          )}

          <p className="skill-picker-hint text-xs text-muted-foreground">
            Your default skills apply to every chat; unchecking one here turns it off
            for this chat only. Change the default set from “Manage skills…”. Bundled
            scripts, assets, and evals are never sent to a model.
          </p>
        </div>
      )}
    </div>
  );
}
