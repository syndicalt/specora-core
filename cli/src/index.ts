#!/usr/bin/env node

// Load .env FIRST
import './env.js';

import * as readline from 'readline';
import chalk from 'chalk';
import ora from 'ora';
import { spawnSync, spawn } from 'child_process';
import { resolve } from 'path';
import { SLASH_COMMANDS, parseInput } from './registry.js';
import { loadSettings } from './settings.js';
import { createSession, saveSession, listSessions, type Session } from './session.js';

const PROJECT_ROOT = resolve(process.cwd());

// ─── Brand ──────────────────────────────────────────────────────────

const LOGO = chalk.magenta(`
  ███████╗██████╗ ███████╗ ██████╗ ██████╗ ██████╗  █████╗
  ██╔════╝██╔══██╗██╔════╝██╔════╝██╔═══██╗██╔══██╗██╔══██╗
  ███████╗██████╔╝█████╗  ██║     ██║   ██║██████╔╝███████║
  ╚════██║██╔═══╝ ██╔══╝  ██║     ██║   ██║██╔══██╗██╔══██║
  ███████║██║     ███████╗╚██████╗╚██████╔╝██║  ██║██║  ██║
  ╚══════╝╚═╝     ╚══════╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝
`);

const TIPS = [
  'Try "validate my contracts" — natural language works here',
  '/help shows all slash commands',
  '/new launches the domain builder interview',
  '! runs shell commands inline (e.g. ! ls domains/)',
  '/heal auto-fixes validation errors',
];

// ─── Command Map ────────────────────────────────────────────────────

const INTERACTIVE_COMMANDS = new Set(['/new', '/add', '/refine', '/chat']);

const COMMAND_MAP: Record<string, (args: string[]) => { cmd: string; jsonMode: boolean }> = {
  '/validate': (args) => ({ cmd: `specora forge validate ${args[0] || 'domains/'} --output json`, jsonMode: true }),
  '/compile':  (args) => ({ cmd: `specora forge compile ${args[0] || 'domains/'} --output json`, jsonMode: true }),
  '/generate': (args) => ({ cmd: `specora forge generate ${args[0] || 'domains/'}`, jsonMode: false }),
  '/graph':    (args) => ({ cmd: `specora forge graph ${args[0] || 'domains/'} --output json`, jsonMode: true }),
  '/new':      ()     => ({ cmd: `specora factory new`, jsonMode: false }),
  '/add':      (args) => ({ cmd: `specora factory add ${args.join(' ')}`, jsonMode: false }),
  '/explain':  (args) => ({ cmd: `specora factory explain ${args[0] || ''}`, jsonMode: false }),
  '/refine':   (args) => {
    const path = args[0] || '';
    const instruction = args.slice(1).join(' ');
    return { cmd: `specora factory refine "${path}" "${instruction}"`, jsonMode: false };
  },
  '/chat':      (args) => ({ cmd: `specora factory chat ${args.join(' ')}`, jsonMode: false }),
  '/heal':      (args) => ({ cmd: `specora healer fix ${args[0] || 'domains/'}`, jsonMode: false }),
  '/status':    ()     => ({ cmd: `specora healer status --output json`, jsonMode: true }),
  '/tickets':   ()     => ({ cmd: `specora healer tickets --output json`, jsonMode: true }),
  '/history':   ()     => ({ cmd: `specora healer history`, jsonMode: false }),
  '/visualize': (args) => ({ cmd: `specora factory visualize ${args.join(' ') || 'domains/'}`, jsonMode: false }),
};

// ─── Session ────────────────────────────────────────────────────────

const session: Session = createSession();
let messageCount = 0;

function logSession(input: string, output: string, isError = false) {
  session.entries.push({ input, output, isError, timestamp: new Date().toISOString() });
  saveSession(session);
}

// ─── Output helpers ─────────────────────────────────────────────────

function printUser(text: string) {
  console.log(chalk.bold(`❯ ${text}`));
}

function printResponse(text: string) {
  for (const line of text.split('\n')) {
    console.log(`  ${line}`);
  }
  console.log();
}

function printError(text: string) {
  for (const line of text.split('\n')) {
    console.log(chalk.red(`  ${line}`));
  }
  console.log();
}

function printTool(name: string) {
  console.log(chalk.cyan.dim(`  ⚡ ${name}`));
}

function printSystem(text: string) {
  console.log(chalk.yellow.dim(`  ${text}`));
}

// ─── Command Execution ──────────────────────────────────────────────

function runInteractive(cmd: string): { success: boolean; output: string } {
  console.log();  // blank line before interactive command
  const result = spawnSync(cmd, {
    shell: true,
    cwd: PROJECT_ROOT,
    env: { ...process.env },
    stdio: 'inherit',
    timeout: 300000,
  });
  console.log();  // blank line after
  return {
    success: result.status === 0,
    output: result.status === 0 ? 'Done.' : `Exited with code ${result.status}`,
  };
}

function runCapture(cmd: string): Promise<{ success: boolean; output: string; json?: any }> {
  return new Promise((resolve) => {
    const proc = spawn(cmd, {
      shell: true,
      cwd: PROJECT_ROOT,
      env: { ...process.env },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.stderr.on('data', (d: Buffer) => { stderr += d.toString(); });

    proc.on('close', (code) => {
      const output = stdout.trim() || stderr.trim();
      resolve({ success: code === 0, output: output || (code === 0 ? 'Done.' : 'Failed.') });
    });

    proc.on('error', (err) => {
      resolve({ success: false, output: `Failed: ${err.message}` });
    });

    setTimeout(() => { proc.kill(); resolve({ success: false, output: 'Timed out (60s)' }); }, 60000);
  });
}

async function routeNaturalLanguage(input: string): Promise<{ success: boolean; output: string }> {
  const result = await runCapture(`python -m healer.api.agent "${input.replace(/"/g, '\\"')}"`);
  try {
    const json = JSON.parse(result.output);
    if (!json.command) {
      return { success: true, output: json.explanation || "I'm not sure how to help. Try /help." };
    }
    printTool(json.command);
    const cmdResult = await runCapture(`specora ${json.command}`);
    const prefix = json.explanation ? `${json.explanation}\n\n` : '';
    return { success: cmdResult.success, output: prefix + cmdResult.output };
  } catch {
    return result;
  }
}

function formatJson(data: any): string {
  if (data.valid !== undefined) {
    if (data.valid) return chalk.green(`✓ All ${data.contract_count} contracts are valid`);
    const errors = data.errors || [];
    return chalk.red(`✗ ${errors.length} error(s) in ${data.contract_count} contracts\n`) +
      errors.map((e: any) => `  ${e.fqn}: ${e.message}`).join('\n');
  }
  if (data.success !== undefined) {
    if (data.success) return chalk.green(`✓ Compiled: ${data.summary}`);
    return chalk.red(`✗ Compilation failed:\n`) + (data.errors || []).join('\n');
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
      `  ${(t.id || '').slice(0, 8)} [${t.status}] ${t.priority} T${t.tier} ${t.contract_fqn || '?'}: ${(t.error || t.raw_error || '').slice(0, 40)}`
    ).join('\n');
  }
  return JSON.stringify(data, null, 2);
}

// ─── Handle Input ───────────────────────────────────────────────────

async function handleInput(line: string, rl: readline.Interface): Promise<void> {
  const trimmed = line.trim();
  if (!trimmed) return;

  messageCount++;
  printUser(trimmed);

  // ── Local commands ──
  if (trimmed === '/exit' || trimmed === '/quit') {
    saveSession(session);
    console.log(chalk.dim('  Goodbye.'));
    process.exit(0);
  }

  if (trimmed === '/clear') {
    console.clear();
    console.log(LOGO);
    return;
  }

  if (trimmed === '/help') {
    const lines = Object.entries(SLASH_COMMANDS)
      .map(([cmd, desc]) => `  ${chalk.cyan(cmd.padEnd(24))} ${chalk.dim(desc)}`)
      .join('\n');
    console.log(lines);
    console.log();
    logSession(trimmed, 'help');
    return;
  }

  if (trimmed === '/settings') {
    const settings = loadSettings();
    printResponse(JSON.stringify(settings, null, 2));
    logSession(trimmed, 'settings');
    return;
  }

  if (trimmed === '/resume') {
    const sessions = listSessions();
    if (sessions.length === 0) {
      printResponse('No previous sessions.');
    } else {
      const list = sessions.slice(0, 10).map(s =>
        `  ${s.id.slice(0, 8)}  ${s.updated_at.slice(0, 16)}  ${s.entries} msgs`
      ).join('\n');
      printResponse(`Recent sessions:\n${list}`);
    }
    logSession(trimmed, 'resume');
    return;
  }

  // ── Parse input ──
  const parsed = parseInput(trimmed);

  // ── Shell escape ──
  if (parsed.type === 'shell') {
    printTool(`$ ${parsed.command}`);
    const result = runInteractive(parsed.command);
    logSession(trimmed, result.output, !result.success);
    return;
  }

  // ── Slash commands ──
  if (parsed.type === 'slash') {
    const builder = COMMAND_MAP[parsed.command];
    if (!builder) {
      printError(`Unknown command: ${parsed.command}\nType /help for available commands.`);
      return;
    }

    const { cmd, jsonMode } = builder(parsed.args);

    // Interactive commands take over the terminal
    if (INTERACTIVE_COMMANDS.has(parsed.command)) {
      printTool(cmd);
      const result = runInteractive(cmd);
      logSession(trimmed, result.output, !result.success);
      return;
    }

    // Non-interactive: capture output with spinner
    printTool(parsed.command);
    const spinner = ora({ text: 'Running…', color: 'magenta' }).start();

    try {
      const result = await runCapture(cmd);
      spinner.stop();

      if (jsonMode && result.success) {
        try {
          const json = JSON.parse(result.output);
          printResponse(formatJson(json));
          logSession(trimmed, result.output);
        } catch {
          printResponse(result.output);
          logSession(trimmed, result.output);
        }
      } else if (result.success) {
        printResponse(result.output);
        logSession(trimmed, result.output);
      } else {
        printError(result.output);
        logSession(trimmed, result.output, true);
      }
    } catch (err: any) {
      spinner.stop();
      printError(err.message);
      logSession(trimmed, err.message, true);
    }
    return;
  }

  // ── Natural language ──
  printTool('Routing via agent…');
  const spinner = ora({ text: 'Thinking…', color: 'magenta' }).start();

  try {
    const result = await routeNaturalLanguage(trimmed);
    spinner.stop();
    if (result.success) {
      printResponse(result.output);
    } else {
      printError(result.output);
    }
    logSession(trimmed, result.output, !result.success);
  } catch (err: any) {
    spinner.stop();
    printError(err.message);
    logSession(trimmed, err.message, true);
  }
}

// ─── Main ───────────────────────────────────────────────────────────

function main() {
  const tip = TIPS[Math.floor(Math.random() * TIPS.length)];

  console.log(LOGO);
  console.log(chalk.dim(`  Contract-Driven Development Engine`));
  console.log(chalk.dim(`  ${tip}`));
  console.log();

  const PROMPT = '❯ ';

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: PROMPT,
    terminal: true,
  });

  rl.prompt();

  rl.on('line', async (line) => {
    await handleInput(line, rl);
    rl.prompt();
  });

  rl.on('close', () => {
    saveSession(session);
    console.log(chalk.dim('\n  Goodbye.'));
    process.exit(0);
  });

  // Handle Ctrl+C gracefully
  process.on('SIGINT', () => {
    saveSession(session);
    console.log(chalk.dim('\n  Goodbye.'));
    process.exit(0);
  });
}

main();
