import { button, escapeHtml, layout, type Email } from "../email";

export function magicLinkEmail(url: string, siteUrl: string): Omit<Email, "to"> {
  const subject = "Your clipbench sign-in link";
  const text = [
    "Sign in to clipbench with this link:",
    "",
    url,
    "",
    "The link works once and expires in 10 minutes.",
    "If you did not ask for it, ignore this email.",
    "",
    siteUrl,
  ].join("\n");
  const html = layout(
    subject,
    `<p style="margin:0 0 8px;">Use this link to sign in to clipbench.</p>
${button(url, "Sign in")}
<p style="margin:0;color:#6b6b66;font-size:13px;">It works once and expires in 10 minutes. If the button does not work, paste this into your browser:<br><span style="word-break:break-all;">${escapeHtml(url)}</span></p>`,
    `If you did not ask for this, ignore it. <a href="${escapeHtml(siteUrl)}" style="color:#6b6b66;">${escapeHtml(siteUrl)}</a>`,
  );
  return { subject, text, html };
}
