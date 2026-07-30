"""Generate the Next.js project scaffold — package.json, configs, shared libs."""
from __future__ import annotations

import json

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile


def generate_scaffold(ir: DomainIR) -> list[GeneratedFile]:
    """Generate project configuration and the runtime helpers pages depend on."""
    return [
        _package_json(ir),
        _next_config(ir),
        _tailwind_config(ir),
        _postcss_config(ir),
        _tsconfig(ir),
        _utils(ir),
        _form_lib(ir),
        _pagination_lib(ir),
    ]


def _package_json(ir: DomainIR) -> GeneratedFile:
    data = {
        "name": f"{ir.domain}-frontend",
        "version": "0.2.0",
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
            # lucide-react was listed but never imported. An unused dependency
            # is a supply-chain surface and an install cost for nothing.
            "clsx": "^2.1.0",
            "tailwind-merge": "^2.3.0",
            "class-variance-authority": "^0.7.0",
        },
        "devDependencies": {
            "typescript": "^5.6.0",
            "@types/react": "^18.3.0",
            "@types/react-dom": "^18.3.0",
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
    return GeneratedFile(
        path="frontend/next.config.js",
        content=content,
        provenance=f"domain/{ir.domain}",
    )


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
    return GeneratedFile(
        path="frontend/tailwind.config.js",
        content=content,
        provenance=f"domain/{ir.domain}",
    )


def _postcss_config(ir: DomainIR) -> GeneratedFile:
    content = """module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
"""
    return GeneratedFile(
        path="frontend/postcss.config.js",
        content=content,
        provenance=f"domain/{ir.domain}",
    )


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
    return GeneratedFile(
        path="frontend/src/lib/utils.ts",
        content=content,
        provenance=f"domain/{ir.domain}",
    )


def _form_lib(ir: DomainIR) -> GeneratedFile:
    """Type-aware coercion and validation for every generated form."""
    content = '''/**
 * Turn a submitted form into a typed API payload.
 *
 * The handler this replaces was:
 *
 *     formData.forEach((v, k) => { if (v) obj[k] = v; });
 *
 * which lost data three ways. `0` and `false` are falsy, so a quantity of zero
 * never reached the server. An unchecked checkbox is absent from `FormData`
 * altogether, so a boolean could be switched on and never off. And every value
 * was sent as a string, so a typed column got `"5"` where it wanted `5`.
 */

export type FieldKind = "string" | "integer" | "number" | "decimal" | "boolean" | "json";

export interface FieldSpec {
  kind: FieldKind;
  required: boolean;
  label: string;
}

export interface CoercedForm {
  values: Record<string, unknown>;
  errors: Record<string, string>;
}

export function coerceForm(
  form: HTMLFormElement,
  specs: Record<string, FieldSpec>,
): CoercedForm {
  const data = new FormData(form);
  const values: Record<string, unknown> = {};
  const errors: Record<string, string> = {};

  for (const [name, spec] of Object.entries(specs)) {
    if (spec.kind === "boolean") {
      // Absence means unchecked, which is `false` \u2014 not "leave it alone".
      values[name] = data.get(name) !== null;
      continue;
    }

    const raw = data.get(name);
    const text = typeof raw === "string" ? raw.trim() : "";

    if (text === "") {
      if (spec.required) errors[name] = `${spec.label} is required.`;
      // An omitted optional field is left out of the payload rather than sent
      // as "", which a typed column rejects.
      continue;
    }

    switch (spec.kind) {
      case "integer": {
        if (!/^-?\\d+$/.test(text)) {
          errors[name] = `${spec.label} must be a whole number.`;
          break;
        }
        const parsed = Number(text);
        if (!Number.isSafeInteger(parsed)) {
          errors[name] = `${spec.label} is too large.`;
          break;
        }
        values[name] = parsed;
        break;
      }
      case "number": {
        const parsed = Number(text);
        if (!Number.isFinite(parsed)) {
          errors[name] = `${spec.label} must be a number.`;
          break;
        }
        values[name] = parsed;
        break;
      }
      case "decimal": {
        if (!/^-?\\d+(\\.\\d+)?$/.test(text)) {
          errors[name] = `${spec.label} must be an amount, for example 12.34.`;
          break;
        }
        // Sent as a string, deliberately. A JSON number is a double, and a
        // double cannot hold 0.1 \u2014 which is the whole reason `decimal` is a
        // separate type from `number`.
        values[name] = text;
        break;
      }
      case "json": {
        try {
          values[name] = JSON.parse(text);
        } catch {
          errors[name] = `${spec.label} must be valid JSON.`;
        }
        break;
      }
      default:
        values[name] = text;
    }
  }

  return { values, errors };
}
'''
    return GeneratedFile(
        path="frontend/src/lib/form.ts",
        content=content,
        provenance=f"domain/{ir.domain}",
    )


def _pagination_lib(ir: DomainIR) -> GeneratedFile:
    """The keyset-pagination hook every list view is built on."""
    content = '''"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { errorMessage, type ListPage, type ListParams } from "./api";

/**
 * Accumulating keyset pagination.
 *
 * There is no page number and no total, by design: the API returns an opaque
 * `next_cursor` and nothing else, because counting rows is the full scan that
 * offset paging was removed to avoid. Views append pages instead.
 */
export interface CursorList<T> {
  items: T[];
  /** First page in flight. */
  loading: boolean;
  /** A subsequent page in flight. */
  loadingMore: boolean;
  /** Safe to render; never the server's own text. */
  error: string | null;
  hasMore: boolean;
  loadMore: () => void;
  reload: () => void;
}

/**
 * @param fetchPage Must be referentially stable across renders \u2014 the generated
 *   call sites pass a module-level api-client binding. An inline arrow would
 *   restart the query on every render.
 */
export function useCursorList<T>(
  fetchPage: (params: ListParams) => Promise<ListPage<T>>,
): CursorList<T> {
  const [items, setItems] = useState<T[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  // Every load is tagged with the generation it started in. A page that
  // resolves after a reload began belongs to the old generation and is
  // dropped, instead of appending stale rows beneath fresh ones.
  const generation = useRef(0);
  const inFlight = useRef(false);

  const load = useCallback(
    async (after: string | null) => {
      if (inFlight.current) return;
      inFlight.current = true;

      const mine = generation.current;
      if (after === null) setLoading(true);
      else setLoadingMore(true);
      setError(null);

      try {
        const page = await fetchPage({ cursor: after });
        if (mine !== generation.current) return;
        setItems((current) => (after === null ? page.items : [...current, ...page.items]));
        setCursor(page.next_cursor);
      } catch (cause) {
        if (mine !== generation.current) return;
        setError(errorMessage(cause));
      } finally {
        inFlight.current = false;
        if (mine === generation.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [fetchPage],
  );

  const reload = useCallback(() => {
    generation.current += 1;
    inFlight.current = false;
    setCursor(null);
    setItems([]);
    void load(null);
  }, [load]);

  useEffect(() => {
    reload();
  }, [reload]);

  const loadMore = useCallback(() => {
    if (cursor !== null) void load(cursor);
  }, [cursor, load]);

  return {
    items,
    loading,
    loadingMore,
    error,
    hasMore: cursor !== null,
    loadMore,
    reload,
  };
}
'''
    return GeneratedFile(
        path="frontend/src/lib/pagination.ts",
        content=content,
        provenance=f"domain/{ir.domain}",
    )
