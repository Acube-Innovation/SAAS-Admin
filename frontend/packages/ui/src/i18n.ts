/**
 * Minimal translation layer: every UI string is looked up by key.
 * English is the only locale for now; more locales add another messages object.
 */
export type Messages = Record<string, string>;

export function createTranslator(messages: Messages) {
  return function t(key: string, params: Record<string, string | number> = {}): string {
    const template = messages[key];
    if (template === undefined) {
      return key;
    }
    return template.replace(/\{(\w+)\}/g, (match, name: string) =>
      name in params ? String(params[name]) : match,
    );
  };
}
