/** Scoped accessibility fixes for the upstream Swagger UI, preserving its executor. */
export function docsHTML(html: string) {
  const enhancements = `<style>
    .swagger-ui .opblock-summary-method,
    .swagger-ui .info .title small,
    .swagger-ui .info .title small pre { background:#163d32!important; color:#fffefa!important; text-shadow:none!important; }
    .swagger-ui a, .swagger-ui .info a { color:#1b5d4b!important; }
    .swagger-ui .btn.authorize, .swagger-ui .btn.cancel { color:#163d32; border-color:#163d32; }
    .swagger-ui .parameter__name.required span, .swagger-ui .parameter__name.required:after { color:#993b2d; }
    .swagger-ui .parameter__in, .swagger-ui .parameter__extension,
    .swagger-ui .json-schema-2020-12-expand-deep-button { color:#163d32!important; }
    .swagger-ui .btn.execute, .swagger-ui .download-contents { background:#163d32!important; color:#fffefa!important; border-color:#163d32!important; }
    .swagger-ui pre.microlight { background:#163d32!important; color:#fffefa!important; }
    .swagger-ui pre.microlight * { color:inherit!important; }
    .swagger-ui :focus-visible { outline:3px solid #537a16; outline-offset:3px; }
  </style><script>
    (() => {
      // Attribute annotations preserve React's elements, handlers and Try it out.
      const annotate = () => {
        document.querySelectorAll('.opblock-tag, .models > h4').forEach(heading => heading.setAttribute('aria-level', '2'));
        document.querySelectorAll('.opblock-title').forEach(heading => heading.setAttribute('aria-level', '3'));
        const introduction = document.querySelector('.information-container');
        if (introduction) { introduction.setAttribute('role', 'banner'); introduction.setAttribute('aria-label', 'Описание API'); }
        document.querySelectorAll('pre.microlight').forEach(code => code.setAttribute('tabindex', '0'));
      };
      new MutationObserver(annotate).observe(document.getElementById('swagger-ui'), {childList:true, subtree:true});
      annotate();
    })();
  </script>`;
  return html
    .replace(/<html(?:\s[^>]*)?>/i, '<html lang="ru">')
    // The operation remains a disclosure button. A link nested inside it creates
    // an inaccessible second keyboard target; use the normal text path instead.
    .replace(/"deepLinking"\s*:\s*true/g, '"deepLinking": false')
    .replaceAll("url: '/openapi.json'", "url: '/api/openapi.json'")
    .replace('</body>', `${enhancements}</body>`);
}
