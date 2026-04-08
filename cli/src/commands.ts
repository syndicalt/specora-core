import { spawn, spawnSync } from 'child_process';
import { resolve } from 'path';
import { parseInput } from './registry.js';

// Project root — where specora CLI and domains/ live
const PROJECT_ROOT = resolve(process.cwd());

export interface CommandResult {
  success: boolean;
  output: string;
  json?: any;
  interactive?: boolean;  // true = command took over the terminal
}

// Commands that need terminal interaction (stdin/stdout passthrough)
const INTERACTIVE_COMMANDS = new Set(['/new', '/add', '/refine', '/chat']);

const COMMAND_MAP: Record<string, (args: string[]) => { cmd: string; jsonMode: boolean }> = {
  '/validate': (args) => ({ cmd: `specora forge validate ${args[0] || 'domains/'} --output json`, jsonMode: true }),
  '/compile': (args) => ({ cmd: `specora forge compile ${args[0] || 'domains/'} --output json`, jsonMode: true }),
  '/generate': (args) => ({ cmd: `specora forge generate ${args[0] || 'domains/'}`, jsonMode: false }),
  '/graph': (args) => ({ cmd: `specora forge graph ${args[0] || 'domains/'} --output json`, jsonMode: true }),
  '/new': () => ({ cmd: `specora factory new`, jsonMode: false }),
  '/add': (args) => ({ cmd: `specora factory add ${args.join(' ')}`, jsonMode: false }),
  '/explain': (args) => ({ cmd: `specora factory explain ${args[0] || ''}`, jsonMode: false }),
  '/refine': (args) => {
    const path = args[0] || '';
    const instruction = args.slice(1).join(' ');
    return { cmd: `specora factory refine "${path}" "${instruction}"`, jsonMode: false };
  },
  '/chat': (args) => ({ cmd: `specora factory chat ${args.join(' ')}`, jsonMode: false }),
  '/heal': (args) => ({ cmd: `specora healer fix ${args[0] || 'domains/'}`, jsonMode: false }),
  '/status': () => ({ cmd: `specora healer status --output json`, jsonMode: true }),
  '/tickets': () => ({ cmd: `specora healer tickets --output json`, jsonMode: true }),
  '/history': () => ({ cmd: `specora healer history`, jsonMode: false }),
  '/visualize': (args) => ({ cmd: `specora factory visualize ${args.join(' ') || 'domains/'}`, jsonMode: false }),
};

export function executeCommand(input: string): Promise<CommandResult> {
  const parsed = parseInput(input);

  if (parsed.type === 'shell') {
    return runInteractive(parsed.command);
  }

  if (parsed.type === 'slash') {
    const builder = COMMAND_MAP[parsed.command];
    if (!builder) {
      return Promise.resolve({
        success: false,
        output: `Unknown command: ${parsed.command}\nType /help for available commands.`,
      });
    }
    const { cmd, jsonMode } = builder(parsed.args);

    // Interactive commands hand over the terminal entirely
    if (INTERACTIVE_COMMANDS.has(parsed.command)) {
      return runInteractive(cmd);
    }

    return runCapture(cmd, jsonMode);
  }

  if (parsed.type === 'natural') {
    return routeNaturalLanguage(parsed.raw);
  }

  return Promise.resolve({
    success: false,
    output: `Unknown input type. Type /help for available commands.`,
  });
}

/**
 * Run a command interactively — hands over stdin/stdout/stderr to the child.
 * The REPL UI pauses while this runs. The user interacts directly with the process.
 */
function runInteractive(cmd: string): Promise<CommandResult> {
  return new Promise((resolve) => {
    const result = spawnSync(cmd, {
      shell: true,
      cwd: PROJECT_ROOT,
      env: { ...process.env },
      stdio: 'inherit',  // Full terminal passthrough
      timeout: 300000,    // 5 min for interactive commands
    });

    resolve({
      success: result.status === 0,
      output: result.status === 0 ? 'Done.' : `Exited with code ${result.status}`,
      interactive: true,
    });
  });
}

/**
 * Run a command and capture its output (non-interactive).
 */
function runCapture(cmd: string, jsonMode: boolean = false): Promise<CommandResult> {
  return new Promise((resolve) => {
    const proc = spawn(cmd, {
      shell: true,
      cwd: PROJECT_ROOT,
      env: { ...process.env },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data: Buffer) => { stdout += data.toString(); });
    proc.stderr.on('data', (data: Buffer) => { stderr += data.toString(); });

    proc.on('close', (code) => {
      const output = stdout.trim() || stderr.trim();
      const success = code === 0;

      if (jsonMode && success && output) {
        try {
          const json = JSON.parse(output);
          resolve({ success, output: formatJson(json), json });
        } catch {
          resolve({ success, output });
        }
      } else {
        resolve({ success, output: output || (success ? 'Done.' : 'Command failed.') });
      }
    });

    proc.on('error', (err) => {
      resolve({ success: false, output: `Failed to execute: ${err.message}` });
    });

    setTimeout(() => {
      proc.kill();
      resolve({ success: false, output: 'Command timed out (60s)' });
    }, 60000);
  });
}

async function routeNaturalLanguage(input: string): Promise<CommandResult> {
  const agentResult = await runCapture(
    `python -m healer.api.agent "${input.replace(/"/g, '\\"')}"`,
    true
  );

  if (!agentResult.success || !agentResult.json) {
    return { success: false, output: agentResult.output || 'Agent routing failed' };
  }

  const { command, explanation } = agentResult.json;

  if (!command) {
    return { success: true, output: explanation || "I'm not sure how to help with that. Try /help." };
  }

  const prefix = explanation ? `${explanation}\n\n` : '';
  const result = await runCapture(`specora ${command}`);
  return {
    success: result.success,
    output: prefix + result.output,
  };
}

export function runProcessStreaming(
  cmd: string,
  onData: (chunk: string) => void,
): Promise<CommandResult> {
  return new Promise((resolve) => {
    const proc = spawn(cmd, {
      shell: true,
      cwd: PROJECT_ROOT,
      env: { ...process.env },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data: Buffer) => {
      const chunk = data.toString();
      stdout += chunk;
      onData(chunk);
    });

    proc.stderr.on('data', (data: Buffer) => { stderr += data.toString(); });

    proc.on('close', (code) => {
      resolve({ success: code === 0, output: stdout.trim() || stderr.trim() });
    });

    proc.on('error', (err) => {
      resolve({ success: false, output: `Failed: ${err.message}` });
    });

    setTimeout(() => {
      proc.kill();
      resolve({ success: false, output: 'Timed out (60s)' });
    }, 60000);
  });
}

function formatJson(data: any): string {
  if (data.valid !== undefined) {
    if (data.valid) return `✓ All ${data.contract_count} contracts are valid`;
    const errors = data.errors || [];
    return `✗ ${errors.length} error(s) in ${data.contract_count} contracts\n` +
      errors.map((e: any) => `  ${e.fqn}: ${e.message}`).join('\n');
  }
  if (data.success !== undefined) {
    if (data.success) return `✓ Compiled: ${data.summary}`;
    return `✗ Compilation failed:\n` + (data.errors || []).join('\n');
  }
  if (data.nodes) {
    return `Contract graph (${data.count} contracts):\n` +
      data.nodes.map((n: any) => `  ${n.fqn} → [${n.dependencies.join(', ')}]`).join('\n');
  }
  if (data.by_status !== undefined) {
    const entries = Object.entries(data.by_status).map(([k, v]) => `  ${k}: ${v}`);
    return `Healer Queue:\n${entries.join('\n')}\n  total: ${data.total}`;
  }
  if (Array.isArray(data)) {
    if (data.length === 0) return 'No tickets found.';
    return data.map((t: any) =>
      `  ${t.id?.slice(0, 8)} [${t.status}] ${t.priority} T${t.tier} ${t.contract_fqn || '?'}: ${(t.error || t.raw_error || '').slice(0, 40)}`
    ).join('\n');
  }
  return JSON.stringify(data, null, 2);
}
