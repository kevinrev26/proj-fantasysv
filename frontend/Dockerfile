FROM nginx:alpine

# Copy custom nginx config (optional, for clean routing)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy static files (HTML, CSS, JS)
COPY static /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
