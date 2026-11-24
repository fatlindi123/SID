import { useEffect, useRef, useState } from "react";
import type { AuthenticatedUser } from "../apiClient";

export function avatarInitials(value: string): string {
  return value.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase() || "").join("") || "I";
}

export function AccountMenu({
  anchor,
  close,
  navigate,
  user,
  onEditProfile,
  onLogout,
}: {
  anchor: HTMLElement | null;
  close: () => void;
  navigate: (workspace: string) => void;
  user: AuthenticatedUser;
  onEditProfile: () => void;
  onLogout: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    if (!anchor) {
      setPosition(null);
      return;
    }
    const updatePosition = () => {
      const bounds = anchor.getBoundingClientRect();
      const fromSidebar = anchor.closest(".sidebar") !== null;
      const menuHeight = menuRef.current?.offsetHeight || 250;
      const below = bounds.bottom + 8;
      const above = bounds.top - menuHeight - 8;
      const preferred = fromSidebar ? above : below;
      const alternate = fromSidebar ? below : above;
      const top = preferred >= 8 && preferred + menuHeight <= window.innerHeight - 8
        ? preferred
        : Math.max(8, Math.min(alternate, window.innerHeight - menuHeight - 8));
      const menuWidth = menuRef.current?.offsetWidth || 250;
      const preferredLeft = fromSidebar ? bounds.right + 8 : bounds.right - menuWidth;
      setPosition({
        top,
        left: Math.max(8, Math.min(preferredLeft, window.innerWidth - menuWidth - 8)),
      });
    };
    updatePosition();
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node) && !anchor.contains(event.target as Node)) close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close();
        anchor.focus();
        return;
      }
      const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role='menuitem']") || []);
      if (!items.length) return;
      const activeIndex = items.indexOf(document.activeElement as HTMLButtonElement);
      let nextIndex: number | null = null;
      if (event.key === "ArrowDown") nextIndex = (activeIndex + 1) % items.length;
      else if (event.key === "ArrowUp") nextIndex = (activeIndex <= 0 ? items.length : activeIndex) - 1;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = items.length - 1;
      if (nextIndex !== null) { event.preventDefault(); items[nextIndex]?.focus(); }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [anchor, close]);

  useEffect(() => {
    if (anchor && position) menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [anchor, position]);

  if (!anchor || !position) return null;
  return (
    <div className="workspace-menu account-menu" id="account-menu" ref={menuRef} role="menu" aria-label="Account menu" style={{ top: position.top, left: position.left }}>
      <div className="workspace-menu-identity account-menu-identity">
        <span className="account-menu-avatar">{avatarInitials(user.full_name)}</span>
        <span className="account-menu-user"><strong>{user.full_name}</strong><span>{user.email}</span><small>Email verified</small></span>
      </div>
      <button role="menuitem" type="button" onClick={() => { close(); onEditProfile(); }}>Edit profile</button>
      <button role="menuitem" type="button" onClick={() => navigate("Overview")}>Overview</button>
      <button role="menuitem" type="button" onClick={() => navigate("Settings")}>Settings</button>
      <div className="account-menu-divider" />
      <button className="account-menu-logout" role="menuitem" type="button" onClick={onLogout}>Sign out</button>
    </div>
  );
}
