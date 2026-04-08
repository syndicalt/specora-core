import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync } from 'fs';
import { join } from 'path';
import { homedir } from 'os';

export interface SessionEntry {
  input: string;
  output: string;
  isError: boolean;
  timestamp: string;
}

export interface Session {
  id: string;
  created_at: string;
  updated_at: string;
  cwd: string;
  entries: SessionEntry[];
}

const SESSIONS_DIR = join(homedir(), '.specora', 'sessions');

export function saveSession(session: Session): void {
  mkdirSync(SESSIONS_DIR, { recursive: true });
  const path = join(SESSIONS_DIR, `${session.id}.json`);
  session.updated_at = new Date().toISOString();
  writeFileSync(path, JSON.stringify(session, null, 2), 'utf-8');
}

export function loadSession(id: string): Session | null {
  const path = join(SESSIONS_DIR, `${id}.json`);
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf-8'));
  } catch {
    return null;
  }
}

export function listSessions(): { id: string; updated_at: string; cwd: string; entries: number }[] {
  if (!existsSync(SESSIONS_DIR)) return [];
  const files = readdirSync(SESSIONS_DIR).filter(f => f.endsWith('.json'));
  return files.map(f => {
    try {
      const session: Session = JSON.parse(readFileSync(join(SESSIONS_DIR, f), 'utf-8'));
      return {
        id: session.id,
        updated_at: session.updated_at,
        cwd: session.cwd,
        entries: session.entries.length,
      };
    } catch {
      return null;
    }
  }).filter(Boolean) as any[];
}

export function createSession(): Session {
  return {
    id: crypto.randomUUID(),
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    cwd: process.cwd(),
    entries: [],
  };
}
