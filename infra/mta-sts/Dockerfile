FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY policies/ /srv/policies/

# La RFC 8461 §3.2 impose des lignes séparées par CRLF. Un dépôt cloné sous Windows (ou
# une normalisation git) peut arriver en LF : on force la fin de ligne à la construction
# plutôt que de dépendre de la façon dont le fichier a été checkouté.
# Le double sed est volontaire : on retire un éventuel CR existant AVANT d'en ajouter un,
# pour ne jamais produire de CRCRLF.
RUN sed -i -e 's/\r$//' -e 's/$/\r/' /srv/policies/*.txt \
    && nginx -t

HEALTHCHECK --interval=30s --timeout=5s \
    CMD wget -qO- http://localhost/healthz || exit 1
