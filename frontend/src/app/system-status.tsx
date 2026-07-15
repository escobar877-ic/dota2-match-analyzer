"use client";

import { useEffect, useState } from "react";

import { fetchFromBackend, SystemReadiness } from "@/lib/api";


type RuntimeState = "checking" | "ready" | "degraded" | "offline";


export function SystemStatus() {
  const [state, setState] = useState<RuntimeState>("checking");
  const [title, setTitle] = useState("Checking backend, model and scheduler readiness.");

  useEffect(() => {
    let active = true;

    async function check() {
      try {
        const report = await fetchFromBackend<SystemReadiness>("/health/ready", {
          cache: "no-store",
        });
        if (!active) return;
        const degraded = report.status !== "ok";
        setState(degraded ? "degraded" : "ready");
        setTitle(
          degraded && report.warnings.length
            ? report.warnings.join(" ")
            : `Model ${report.active_model_version ?? "fallback"}; scheduler ${formatAge(report.scheduler_age_minutes)}.`,
        );
      } catch {
        if (!active) return;
        setState("offline");
        setTitle("Backend readiness check failed.");
      }
    }

    void check();
    const interval = window.setInterval(check, 60_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  return (
    <div className={`system-status system-status-${state}`} title={title} aria-live="polite">
      <span aria-hidden="true" />
      {state === "ready" ? "Ready" : state === "degraded" ? "Degraded" : state === "offline" ? "Offline" : "Checking"}
    </div>
  );
}


function formatAge(value: number | null): string {
  return value == null ? "unknown" : `${Math.round(value)}m old`;
}
