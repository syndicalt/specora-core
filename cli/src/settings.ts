import { readFileSync, existsSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { homedir } from 'os';
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml';

export interface SpecoraSettings {
  llm?: {
    provider?: string;
    model?: string;
  };
  repl?: {
    theme?: string;
    history_size?: number;
    auto_validate?: boolean;
  };
  healer?: {
    webhook_url?: string;
    auto_apply_tier1?: boolean;
  };
}

const DEFAULT_SETTINGS: SpecoraSettings = {
  repl: {
    theme: 'default',
    history_size: 1000,
    auto_validate: true,
  },
  healer: {
    auto_apply_tier1: true,
  },
};

/**
 * Load settings with precedence: local > project > user > defaults
 */
export function loadSettings(): SpecoraSettings {
  const layers: SpecoraSettings[] = [DEFAULT_SETTINGS];

  // User settings: ~/.specora/settings.yaml
  const userPath = join(homedir(), '.specora', 'settings.yaml');
  const userSettings = loadYaml(userPath);
  if (userSettings) layers.push(userSettings);

  // Project settings: .specora/settings.yaml
  const projectPath = join(process.cwd(), '.specora', 'settings.yaml');
  const projectSettings = loadYaml(projectPath);
  if (projectSettings) layers.push(projectSettings);

  // Local settings: .specora/settings.local.yaml (gitignored)
  const localPath = join(process.cwd(), '.specora', 'settings.local.yaml');
  const localSettings = loadYaml(localPath);
  if (localSettings) layers.push(localSettings);

  return deepMerge(...layers);
}

export function saveUserSetting(key: string, value: any): void {
  const dir = join(homedir(), '.specora');
  const path = join(dir, 'settings.yaml');
  mkdirSync(dir, { recursive: true });

  let settings: any = loadYaml(path) || {};
  const parts = key.split('.');
  let current = settings;
  for (let i = 0; i < parts.length - 1; i++) {
    if (!current[parts[i]!]) current[parts[i]!] = {};
    current = current[parts[i]!];
  }
  current[parts[parts.length - 1]!] = value;

  writeFileSync(path, stringifyYaml(settings), 'utf-8');
}

function loadYaml(path: string): SpecoraSettings | null {
  if (!existsSync(path)) return null;
  try {
    const content = readFileSync(path, 'utf-8');
    return parseYaml(content) as SpecoraSettings;
  } catch {
    return null;
  }
}

function deepMerge(...objects: any[]): any {
  const result: any = {};
  for (const obj of objects) {
    for (const key of Object.keys(obj)) {
      if (obj[key] && typeof obj[key] === 'object' && !Array.isArray(obj[key])) {
        result[key] = deepMerge(result[key] || {}, obj[key]);
      } else {
        result[key] = obj[key];
      }
    }
  }
  return result;
}
