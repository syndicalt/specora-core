/**
 * Load .env from the project root before anything else.
 * This ensures spawned Python processes inherit API keys.
 */
import { config } from 'dotenv';
import { resolve, join } from 'path';

// Load from project root (parent of cli/)
const projectRoot = resolve(process.cwd());
config({ path: join(projectRoot, '.env') });

// Also try the parent if we're running from cli/
config({ path: resolve(projectRoot, '..', '.env') });

export const PROJECT_ROOT = projectRoot;
