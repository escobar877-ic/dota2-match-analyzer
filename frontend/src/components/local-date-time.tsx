"use client";

import { useEffect, useState } from "react";

import { formatMatchDate } from "@/lib/api";


export function LocalDateTime({ value }: { value: string | null }) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!value) {
    return <span>Time TBD</span>;
  }

  return (
    <time dateTime={value}>
      {mounted ? formatMatchDate(value) : formatUtcDate(value)}
    </time>
  );
}


function formatUtcDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(value)) + " UTC";
}
