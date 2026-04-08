"""Generate Next.js project scaffold — package.json, configs, utils."""
from __future__ import annotations

import json

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile


def generate_scaffold(ir: DomainIR) -> list[GeneratedFile]:
    """Generate project configuration files."""
    return [
        _package_json(ir),
        _next_config(ir),
        _tailwind_config(ir),
        _postcss_config(ir),
        _tsconfig(ir),
        _utils(ir),
    ]


def _package_json(ir: DomainIR) -> GeneratedFile:
    data = {
        "name": f"{ir.domain}-frontend",
        "version": "0.1.0",
        "private": True,
        "scripts": {
            "dev": "next dev",
            "build": "next build",
            "start": "next start",
            "lint": "next lint",
        },
        "dependencies": {
            "next": "^15.0.0",
            "react": "^18.3.0",
            "react-dom": "^18.3.0",
            "lucide-react": "^0.400.0",
            "clsx": "^2.1.0",
            "tailwind-merge": "^2.3.0",
            "class-variance-authority": "^0.7.0",
        },
        "devDependencies": {
            "typescript": "^5.6.0",
            "@types/react": "^18.3.0",
            "@types/node": "^22.0.0",
            "tailwindcss": "^3.4.0",
            "postcss": "^8.4.0",
            "autoprefixer": "^10.4.0",
        },
    }
    return GeneratedFile(
        path="frontend/package.json",
        content=json.dumps(data, indent=2),
        provenance=f"domain/{ir.domain}",
    )


def _next_config(ir: DomainIR) -> GeneratedFile:
    content = """/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
};

module.exports = nextConfig;
"""
    return GeneratedFile(path="frontend/next.config.js", content=content, provenance=f"domain/{ir.domain}")


def _tailwind_config(ir: DomainIR) -> GeneratedFile:
    content = """/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
};
"""
    return GeneratedFile(path="frontend/tailwind.config.js", content=content, provenance=f"domain/{ir.domain}")


def _postcss_config(ir: DomainIR) -> GeneratedFile:
    content = """module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
"""
    return GeneratedFile(path="frontend/postcss.config.js", content=content, provenance=f"domain/{ir.domain}")


def _tsconfig(ir: DomainIR) -> GeneratedFile:
    data = {
        "compilerOptions": {
            "target": "ES2017",
            "lib": ["dom", "dom.iterable", "esnext"],
            "allowJs": True,
            "skipLibCheck": True,
            "strict": True,
            "noEmit": True,
            "esModuleInterop": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "resolveJsonModule": True,
            "isolatedModules": True,
            "jsx": "preserve",
            "incremental": True,
            "plugins": [{"name": "next"}],
            "paths": {"@/*": ["./src/*"]},
        },
        "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
        "exclude": ["node_modules"],
    }
    return GeneratedFile(
        path="frontend/tsconfig.json",
        content=json.dumps(data, indent=2),
        provenance=f"domain/{ir.domain}",
    )


def _utils(ir: DomainIR) -> GeneratedFile:
    content = """import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(date: string | null | undefined): string {
  if (!date) return "\u2014";
  return new Date(date).toLocaleDateString();
}

export function formatDateTime(date: string | null | undefined): string {
  if (!date) return "\u2014";
  return new Date(date).toLocaleString();
}

export function truncate(str: string, length: number = 50): string {
  if (str.length <= length) return str;
  return str.slice(0, length) + "\u2026";
}
"""
    return GeneratedFile(path="frontend/src/lib/utils.ts", content=content, provenance=f"domain/{ir.domain}")
