/** WhatsApp template content and its spec 2.7 limits. */
export type Button = { text: string; outcome: "confirmed" | "not_available" | "reschedule" };
export type Content = {
  header_text: string;
  body: string;
  footer: string;
  buttons: Button[];
  sample_values: Record<string, string>;
};

export const LIMITS = { header_text: 60, body: 1024, footer: 60, button: 25, buttons: 3 } as const;
const VARIABLE = /\{\{\s*([a-z][a-z0-9_]*)\s*\}\}/g;

export function variablesIn(text: string): string[] {
  return [...text.matchAll(VARIABLE)].map((match) => match[1]!);
}

/** Variables used in the header and body, in order of first use (each needs a sample value). */
export function usedVariables(content: Pick<Content, "header_text" | "body">): string[] {
  return [...new Set([...variablesIn(content.header_text), ...variablesIn(content.body)])];
}

export type PreviewPart = { text: string; variable?: string; missing?: boolean };

/** Split text into literal parts and variables replaced by their sample values. */
export function renderWithSamples(text: string, samples: Record<string, string>): PreviewPart[] {
  const parts: PreviewPart[] = [];
  let last = 0;
  for (const match of text.matchAll(VARIABLE)) {
    if (match.index! > last) parts.push({ text: text.slice(last, match.index) });
    const name = match[1]!;
    const sample = samples[name]?.trim();
    parts.push({ text: sample || `{{${name}}}`, variable: name, missing: !sample });
    last = match.index! + match[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last) });
  return parts;
}

export function emptyContent(): Content {
  return {
    header_text: "",
    body: "",
    footer: "Reply STOP to stop these messages.",
    buttons: [
      { text: "Yes, available", outcome: "confirmed" },
      { text: "Not available", outcome: "not_available" },
      { text: "Change date", outcome: "reschedule" },
    ],
    sample_values: {},
  };
}

/**
 * Fill sample values the console already knows (the client's name for {{agency_name}}) when the
 * user left them empty: Meta needs a sample for every variable.
 */
export function withKnownSamples(content: Content, agencyName: string): Content {
  const samples = { ...content.sample_values };
  if (usedVariables(content).includes("agency_name") && !samples.agency_name?.trim()) {
    samples.agency_name = agencyName;
  }
  return { ...content, sample_values: samples };
}
