import DOMPurify from "dompurify";

export default function SanitizedMessage({ text }: { text: string }) {
  const html = DOMPurify.sanitize(text, {
    ALLOWED_TAGS: ["p", "br", "strong", "b", "em", "i", "code", "ul", "ol", "li"],
    ALLOWED_ATTR: [],
    ALLOW_ARIA_ATTR: false,
    ALLOW_DATA_ATTR: false,
  });
  return <div className="sanitized-message" dangerouslySetInnerHTML={{ __html: html }} />;
}
