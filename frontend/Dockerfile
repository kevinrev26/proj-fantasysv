FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY static /usr/share/nginx/html

# Fix ownership so nginx worker can read the files
RUN chown -R nginx:nginx /usr/share/nginx/html && \
    chmod -R 755 /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
