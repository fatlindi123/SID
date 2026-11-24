import { useEffect, useState } from "react";

export const PREFERENCES_STORAGE_KEY = "software-intelligence.preferences.v1";
export const PREFERENCES_CHANGE_EVENT = "software-intelligence:preferences-change";

export const LANDING_WORKSPACES = [
  "Overview",
  "Projects",
  "Exploration",
  "Knowledge Graph",
  "Workflow Intelligence",
  "Test Cases",
  "Reports",
  "Settings",
] as const;

export const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;

export type LandingWorkspace = (typeof LANDING_WORKSPACES)[number];
export type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];

export type UiPreferences = {
  landingWorkspace: LandingWorkspace;
  pageSize: PageSize;
};

export const DEFAULT_PREFERENCES: UiPreferences = {
  landingWorkspace: "Overview",
  pageSize: 10,
};

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function browserStorage(): StorageLike | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function normalizePreferences(value: unknown): UiPreferences {
  if (!value || typeof value !== "object") return { ...DEFAULT_PREFERENCES };
  const candidate = value as Record<string, unknown>;
  return {
    landingWorkspace: LANDING_WORKSPACES.includes(candidate.landingWorkspace as LandingWorkspace)
      ? (candidate.landingWorkspace as LandingWorkspace)
      : DEFAULT_PREFERENCES.landingWorkspace,
    pageSize: PAGE_SIZE_OPTIONS.includes(candidate.pageSize as PageSize)
      ? (candidate.pageSize as PageSize)
      : DEFAULT_PREFERENCES.pageSize,
  };
}

export function readPreferences(storage: StorageLike | null = browserStorage()): UiPreferences {
  if (!storage) return { ...DEFAULT_PREFERENCES };
  try {
    const saved = storage.getItem(PREFERENCES_STORAGE_KEY);
    return saved ? normalizePreferences(JSON.parse(saved)) : { ...DEFAULT_PREFERENCES };
  } catch {
    return { ...DEFAULT_PREFERENCES };
  }
}

export function savePreferences(value: unknown, storage?: StorageLike): UiPreferences {
  const normalized = normalizePreferences(value);
  if (!value || typeof value !== "object") throw new Error("Choose valid workspace preferences before saving.");
  const candidate = value as Record<string, unknown>;
  if (
    !LANDING_WORKSPACES.includes(candidate.landingWorkspace as LandingWorkspace)
    || !PAGE_SIZE_OPTIONS.includes(candidate.pageSize as PageSize)
  ) {
    throw new Error("Choose a supported landing workspace and page size.");
  }

  const target = storage ?? browserStorage();
  if (!target) throw new Error("Browser storage is unavailable. Preferences were not saved.");
  target.setItem(PREFERENCES_STORAGE_KEY, JSON.stringify(normalized));
  if (!storage && typeof window !== "undefined") window.dispatchEvent(new Event(PREFERENCES_CHANGE_EVENT));
  return normalized;
}

export function resetPreferences(storage?: StorageLike): UiPreferences {
  const target = storage ?? browserStorage();
  if (!target) throw new Error("Browser storage is unavailable. Preferences were not reset.");
  target.removeItem(PREFERENCES_STORAGE_KEY);
  if (!storage && typeof window !== "undefined") window.dispatchEvent(new Event(PREFERENCES_CHANGE_EVENT));
  return { ...DEFAULT_PREFERENCES };
}

export function useUiPreferences(): UiPreferences {
  const [preferences, setPreferences] = useState(readPreferences);

  useEffect(() => {
    const sync = () => setPreferences(readPreferences());
    window.addEventListener(PREFERENCES_CHANGE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(PREFERENCES_CHANGE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  return preferences;
}
