import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { AuthenticatedUser } from "../apiClient";
import { avatarInitials } from "./AccountMenu";

const features = [
  { icon: "explore", title: "Browser Exploration", description: "Explore web applications and discover their pages, interface elements and navigation structure." },
  { icon: "analysis", title: "AI Analysis", description: "Analyze saved exploration data to understand application structure and suggest useful test scenarios." },
  { icon: "cases", title: "Test Cases", description: "Create, organize and manage reusable test cases grounded in saved application evidence." },
  { icon: "run", title: "Test Execution", description: "Run supported browser checks and inspect saved step results and evidence." },
  { icon: "graph", title: "Knowledge Graph", description: "Visualize verified relationships between explorations, pages and interface elements." },
  { icon: "workflow", title: "Workflow Intelligence", description: "Review evidence-backed workflows and their ordered steps." },
  { icon: "reports", title: "Reports", description: "Generate structured project and exploration reports with PDF and JSON exports." },
  { icon: "projects", title: "Projects", description: "Organize explorations, test cases, workflows and related application data." },
];

const workflowSteps = [
  { number: "01", title: "Explore", description: "Discover an application's structure." },
  { number: "02", title: "Understand", description: "Analyze collected evidence and relationships." },
  { number: "03", title: "Organize", description: "Group work and manage reusable test cases." },
  { number: "04", title: "Evaluate", description: "Run supported checks and generate reports." },
];

type FeatureIconName = typeof features[number]["icon"] | "overview";

function FeatureIcon({ name }: { name: FeatureIconName }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return <svg className="welcome-feature-icon" viewBox="0 0 24 24" aria-hidden="true" {...common}>
    {name === "explore" && <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18" /></>}
    {name === "overview" && <><rect x="3.5" y="3.5" width="7" height="7" rx="1.2" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.2" /><rect x="3.5" y="13.5" width="7" height="7" rx="1.2" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.2" /></>}
    {name === "analysis" && <><path d="M12 3v18M8.2 6.5a3.5 3.5 0 0 0-3.1 5.1 3.4 3.4 0 0 0 .7 6.5A3.2 3.2 0 0 0 12 20M15.8 6.5a3.5 3.5 0 0 1 3.1 5.1 3.4 3.4 0 0 1-.7 6.5A3.2 3.2 0 0 1 12 20" /><path d="M8 10h.01M16 10h.01M8.5 14.5h.01M15.5 14.5h.01" /></>}
    {name === "cases" && <><rect x="4" y="3.5" width="16" height="17" rx="3" /><path d="m8 12 2.4 2.4L16.5 8" /></>}
    {name === "run" && <><circle cx="12" cy="12" r="9" /><path d="m10 8 6 4-6 4z" /></>}
    {name === "graph" && <><circle cx="6" cy="6" r="2.3" /><circle cx="18" cy="7" r="2.3" /><circle cx="12" cy="18" r="2.3" /><path d="m8 7 7.6-.1M7.5 8l3.4 7.7m5.7-6.8-3.5 7" /></>}
    {name === "workflow" && <><rect x="3" y="3.5" width="7" height="6" rx="1.5" /><rect x="14" y="14.5" width="7" height="6" rx="1.5" /><path d="M6.5 9.5v4h11v1" /></>}
    {name === "reports" && <><path d="M5 3.5h9l5 5v12H5zM14 3.5v5h5M8 13h8M8 16.5h6" /></>}
    {name === "projects" && <><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z" /></>}
  </svg>;
}

function WelcomeBrandMark() {
  return <span className="welcome-brand-mark" aria-hidden="true"><i /><i /><i /></span>;
}

function ProductPreview() {
  return <div className="welcome-preview-wrap" aria-label="Illustrative preview of the SID workspace">
    <div className="welcome-preview-glow" aria-hidden="true" />
    <div className="welcome-preview">
      <div className="welcome-preview-topbar">
        <div className="welcome-preview-dots"><i /><i /><i /></div>
        <span>SID workspace</span>
        <small><i /> Illustrative preview</small>
      </div>
      <div className="welcome-preview-body">
        <aside className="welcome-preview-sidebar">
          <div className="welcome-preview-brand"><WelcomeBrandMark /><strong>SID</strong></div>
          {[["Overview", "overview"], ["Projects", "projects"], ["Exploration", "explore"], ["Test Cases", "cases"], ["Test Execution", "run"], ["Knowledge Graph", "graph"], ["Workflow Intelligence", "workflow"], ["Reports", "reports"]].map(([label, icon], index) => (
            <div className={`welcome-preview-nav ${index === 0 ? "active" : ""}`} key={label}>
              <FeatureIcon name={icon as FeatureIconName} /><span>{label}</span>
            </div>
          ))}
          <div className="welcome-preview-profile"><span>SI</span><i><b /><b /></i></div>
        </aside>
        <div className="welcome-preview-content">
          <div className="welcome-preview-heading"><div><span>WORKSPACE</span><h3>Overview</h3><p>Your saved software intelligence, in one place.</p></div><span className="welcome-preview-filter">Workspace view <b>⌄</b></span></div>
          <div className="welcome-preview-cards">
            {["Explorations", "Pages & elements", "Test cases", "Execution history"].map((item, index) => <div className="welcome-preview-card" key={item}>
              <span className={`welcome-preview-card-icon tone-${index}`}><FeatureIcon name={(["explore", "graph", "cases", "run"] as FeatureIconName[])[index]} /></span>
              <strong>{item}</strong><i /><i />
            </div>)}
          </div>
          <div className="welcome-preview-lower">
            <div className="welcome-preview-map">
              <div className="welcome-preview-section-title"><strong>Evidence map</strong><span>Illustrative layout</span></div>
              <svg viewBox="0 0 460 180" preserveAspectRatio="none" aria-hidden="true">
                <path d="M72 92 C122 92 120 45 173 45 S228 92 276 92 S331 46 383 46" />
                <path d="M72 92 C123 92 124 139 174 139 S225 92 276 92 S331 137 383 137" />
                <path d="M173 45 C220 45 224 92 276 92" />
              </svg>
              <div className="welcome-map-node map-node-a"><i />Page</div>
              <div className="welcome-map-node map-node-b"><i />Elements</div>
              <div className="welcome-map-node map-node-c"><i />Test case</div>
              <div className="welcome-map-node map-node-d"><i />Workflow</div>
              <div className="welcome-map-node map-node-e"><i />Report</div>
            </div>
            <div className="welcome-preview-actions">
              <div className="welcome-preview-section-title"><strong>Start with</strong><span>Available actions</span></div>
              {["Explore an application", "Review test cases", "Open a report"].map((item, index) => <div key={item}><FeatureIcon name={(["explore", "cases", "reports"] as FeatureIconName[])[index]} /><span>{item}</span><b>→</b></div>)}
            </div>
          </div>
        </div>
      </div>
    </div>
    <div className="welcome-preview-caption"><span /> A product preview, not live workspace data</div>
  </div>;
}

export function WelcomeExperience({
  user,
  accountMenuOpen,
  onOpenAccountMenu,
  onOpenDashboard,
  error,
}: {
  user: AuthenticatedUser;
  accountMenuOpen: boolean;
  onOpenAccountMenu: (anchor: HTMLElement) => void;
  onOpenDashboard: () => Promise<void>;
  error: string | null;
}) {
  const [openingDashboard, setOpeningDashboard] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const featuresRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const cards = Array.from(document.querySelectorAll<HTMLElement>(".welcome-reveal"));
    if (!("IntersectionObserver" in window)) {
      cards.forEach((card) => card.classList.add("is-visible"));
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -24px 0px" });
    cards.forEach((card) => observer.observe(card));
    return () => observer.disconnect();
  }, []);

  const openDashboard = async () => {
    setOpeningDashboard(true);
    setActionError(null);
    try {
      await onOpenDashboard();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "The dashboard could not be opened.");
    } finally {
      setOpeningDashboard(false);
    }
  };
  const scrollTo = (element: HTMLElement | null) => {
    element?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
  };

  return <div className="welcome-page" id="top">
    <div className="welcome-backdrop" aria-hidden="true"><span /><span /><span /></div>
    <header className="welcome-header">
      <a className="welcome-brand" href="#top" aria-label="SID welcome"><WelcomeBrandMark /><strong>SID</strong></a>
      <nav className="welcome-nav" aria-label="Welcome navigation">
        <button type="button" onClick={() => void openDashboard()}>Overview</button>
        <a href="#features" onClick={(event) => { event.preventDefault(); scrollTo(featuresRef.current); }}>Features</a>
        <a href="#about">About</a>
      </nav>
      <div className="welcome-account">
        <span className="welcome-account-name">{user.full_name}</span>
        <button className="welcome-avatar" type="button" aria-label="Open account menu" aria-haspopup="menu" aria-expanded={accountMenuOpen} onClick={(event) => onOpenAccountMenu(event.currentTarget)}>
          {avatarInitials(user.full_name)}<span aria-hidden="true">⌄</span>
        </button>
      </div>
    </header>

    <main>
      <section className="welcome-hero" aria-labelledby="welcome-title">
        <div className="welcome-hero-copy">
          <div className="welcome-eyebrow"><span aria-hidden="true">✧</span> WELCOME TO SID</div>
          <h1 id="welcome-title">Understand your software.<br /><span>Test with confidence.</span></h1>
          <p className="welcome-description">SID helps development and QA teams explore web applications, understand their structure, discover workflows and organize evidence-backed testing in one workspace.</p>
          <div className="welcome-hero-actions">
            <button className="welcome-primary" type="button" disabled={openingDashboard} onClick={() => void openDashboard()}>{openingDashboard ? "Opening dashboard…" : "Open Dashboard"}<span aria-hidden="true">→</span></button>
            <a className="welcome-secondary" href="#features" onClick={(event) => { event.preventDefault(); scrollTo(featuresRef.current); }}><span aria-hidden="true">⌄</span> Explore Features</a>
          </div>
          {(actionError || error) && <p className="welcome-error" role="alert">{actionError || error}</p>}
          <p className="welcome-trust"><span aria-hidden="true">✓</span> Your software intelligence workspace starts here.</p>
        </div>
        <ProductPreview />
      </section>

      <section className="welcome-features" id="features" ref={featuresRef} aria-labelledby="welcome-features-title">
        <div className="welcome-section-heading"><span /><h2 id="welcome-features-title">Everything you need to understand your application</h2><span /></div>
        <div className="welcome-feature-grid">
          {features.map((feature, index) => <article className="welcome-feature-card welcome-reveal" style={{ "--welcome-delay": `${Math.min(index % 4, 3) * 55}ms` } as CSSProperties} key={feature.title}>
            <span className="welcome-feature-icon-wrap"><FeatureIcon name={feature.icon} /></span>
            <div><h3>{feature.title}</h3><p>{feature.description}</p></div>
          </article>)}
        </div>
      </section>

      <section className="welcome-about" id="about" aria-labelledby="welcome-about-title">
        <div className="welcome-about-copy"><p className="welcome-about-eyebrow">A CLEAR PATH FROM DISCOVERY TO CONFIDENCE</p><h2 id="welcome-about-title">From exploration<br />to software intelligence</h2><p>Start with what the browser can verify. Keep discoveries, scenarios and results connected to the evidence behind them.</p></div>
        <div className="welcome-workflow" aria-label="A four-step product workflow">
          {workflowSteps.map((step, index) => <div className="welcome-workflow-step welcome-reveal" style={{ "--welcome-delay": `${index * 65}ms` } as CSSProperties} key={step.number}>
            <span className="welcome-workflow-number">{step.number}</span><h3>{step.title}</h3><p>{step.description}</p>
          </div>)}
        </div>
      </section>

      <section className="welcome-bottom-cta" aria-labelledby="welcome-cta-title">
        <div><span>SID WORKSPACE</span><h2 id="welcome-cta-title">Ready to explore your software?</h2><p>Your dashboard is ready when you are.</p></div>
        <button className="welcome-primary" type="button" disabled={openingDashboard} onClick={() => void openDashboard()}>{openingDashboard ? "Opening dashboard…" : "Open Dashboard"}<span aria-hidden="true">→</span></button>
      </section>
    </main>
    <footer className="welcome-footer"><a className="welcome-footer-brand" href="#top"><WelcomeBrandMark /><strong>SID</strong></a><span>Evidence first. Clear paths from exploration to verification.</span></footer>
  </div>;
}
