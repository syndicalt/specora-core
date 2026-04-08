export const SLASH_COMMANDS: Record<string, string> = {
  '/validate [path]': 'Validate contracts against meta-schemas',
  '/compile [path]': 'Compile contracts to IR',
  '/generate [path]': 'Compile + generate code',
  '/graph [path]': 'Show contract dependency graph',
  '/new': 'Bootstrap a new domain (Factory interview)',
  '/add <kind>': 'Add a single contract to a domain',
  '/explain <path>': 'Explain a contract in plain English',
  '/refine <path> <instruction>': 'Modify a contract via natural language',
  '/heal [path]': 'Run healer on contracts',
  '/status': 'Show healer queue status',
  '/tickets': 'List healer tickets',
  '/history': 'Show healer fix history',
  '/settings': 'Show current settings',
  '/resume': 'List previous sessions',
  '/help': 'Show this help',
  '/clear': 'Clear the screen',
  '/exit': 'Exit the REPL',
  '! <cmd>': 'Run a shell command',
};

export interface ParsedCommand {
  type: 'slash' | 'shell' | 'natural';
  command: string;
  args: string[];
  raw: string;
}

export function parseInput(input: string): ParsedCommand {
  const trimmed = input.trim();

  // Shell escape
  if (trimmed.startsWith('!')) {
    const shellCmd = trimmed.slice(1).trim();
    return { type: 'shell', command: shellCmd, args: [], raw: trimmed };
  }

  // Slash command
  if (trimmed.startsWith('/')) {
    const parts = trimmed.split(/\s+/);
    const cmd = parts[0]!;
    const args = parts.slice(1);
    return { type: 'slash', command: cmd, args, raw: trimmed };
  }

  // Natural language (future: LLM agent routing)
  return { type: 'natural', command: trimmed, args: [], raw: trimmed };
}
