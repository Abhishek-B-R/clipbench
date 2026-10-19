export type Email = { to: string; subject: string; text: string; html: string };

export type SendResult = "sent" | "logged";

/**
 * Send one email through Resend (https://resend.com/docs/api-reference/emails/send-email).
 * Without RESEND_API_KEY the message is logged instead, so local development never sends mail.
 */
export async function sendEmail(env: Env, email: Email): Promise<SendResult> {
  if (!env.RESEND_API_KEY) {
    console.log(JSON.stringify({ event: "email_logged", to: email.to, subject: email.subject, text: email.text }));
    return "logged";
  }
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from: env.EMAIL_FROM, to: [email.to], subject: email.subject, text: email.text, html: email.html }),
  });
  if (!res.ok) {
    const detail = (await res.text()).slice(0, 300);
    throw new Error(`resend ${res.status}: ${detail}`);
  }
  return "sent";
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
}

/** A plain, readable HTML wrapper: one column, system font, no images, no tracking. */
export function layout(title: string, bodyHtml: string, footerHtml: string): string {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(title)}</title></head>
<body style="margin:0;padding:0;background:#f6f6f4;">
<div style="max-width:560px;margin:0 auto;padding:32px 20px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1b1b1a;font-size:15px;line-height:1.55;">
<p style="margin:0 0 24px;font-weight:600;letter-spacing:0.02em;">clipbench</p>
${bodyHtml}
<hr style="border:none;border-top:1px solid #e3e3df;margin:32px 0 16px;">
<p style="margin:0;color:#6b6b66;font-size:13px;">${footerHtml}</p>
</div></body></html>`;
}

export function button(href: string, label: string): string {
  return `<p style="margin:24px 0;"><a href="${escapeHtml(href)}" style="display:inline-block;background:#1b1b1a;color:#ffffff;text-decoration:none;padding:10px 18px;border-radius:6px;font-weight:600;">${escapeHtml(label)}</a></p>`;
}
