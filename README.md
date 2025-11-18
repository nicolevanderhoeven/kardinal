# Kardinal
Kanban Markdown Renderer

A lightweight Flask application that reads an Obsidian Kanban-formatted Markdown file and renders it as a read-only Kanban board.

## Features

- Reads Markdown files in Obsidian Kanban format
- Renders columns (## headers) and cards (- [ ] tasks) as a Kanban board
- Read-only display (no editing capabilities)
- Modern, responsive design
- Lightweight and easy to maintain

## Markdown Format

The application expects Markdown files in Obsidian Kanban format:

```markdown
## Backlog
- [ ] Task 1
- [ ] Task 2

## In Progress
- [ ] Task 3

## Done
- [x] Completed task
```

- **Columns**: Headers starting with `##` (e.g., `## Backlog`)
- **Cards**: Task items using `- [ ]` (incomplete) or `- [x]` (completed)

## Docker Deployment (Recommended)

The easiest way to deploy is using Docker and Docker Compose. You can either:
- **Build on server**: Clone repo and build locally (simpler setup)
- **Use registry**: Build locally/CI, push to Docker Hub, pull on server (faster updates, recommended)

### Prerequisites

- Docker and Docker Compose installed on your server
- Traefik running with a network named `traefik` (or update `docker-compose.yml` to match your network name)
- (Optional) Docker Hub account for registry deployment

### Option 1: Using Docker Registry (Recommended)

This approach builds the image once (locally or via CI/CD) and pushes it to a registry, then pulls it on the server. This is faster and doesn't require git on the server.

#### Building and Pushing the Image

1. **Build the image locally** (or in CI/CD):
```bash
docker build -t YOUR_USERNAME/kardinal:latest .
# Or tag with a version: docker build -t YOUR_USERNAME/kardinal:v1.0.0 .
```

2. **Push to Docker Hub**:
```bash
docker login
docker push YOUR_USERNAME/kardinal:latest
```

Or use GitHub Container Registry:
```bash
docker build -t ghcr.io/YOUR_USERNAME/kardinal:latest .
docker push ghcr.io/YOUR_USERNAME/kardinal:latest
```

#### Deploying on Server

1. **Create a minimal directory** (only docker-compose file needed):
```bash
mkdir -p /path/to/kardinal
cd /path/to/kardinal
```

2. **Copy docker-compose.registry.yml** and update the image name:
```bash
# Edit docker-compose.registry.yml and set YOUR_USERNAME
```

3. **Start the container**:
```bash
docker-compose -f docker-compose.registry.yml up -d
```

#### Updating the Application

1. **Build and push new image** (locally or via CI/CD):
```bash
docker build -t YOUR_USERNAME/kardinal:latest .
docker push YOUR_USERNAME/kardinal:latest
```

2. **On server, pull and restart**:
```bash
cd /path/to/kardinal
docker-compose -f docker-compose.registry.yml pull
docker-compose -f docker-compose.registry.yml up -d
```

### Option 2: Building on Server

This approach builds the image directly on the server from the git repository.

#### Quick Start

1. Clone this repository on your server:
```bash
git clone <your-repo-url> /path/to/kardinal
cd /path/to/kardinal
```

2. Build and start the container:
```bash
docker-compose up -d --build
```

3. The application will be available at `kardinal.nvdh.dev` (assuming Traefik is configured correctly)

#### Updating the Application

When you make changes to the code:

1. Push changes to your git repository
2. On the server, pull the latest changes:
```bash
cd /path/to/kardinal
git pull
```

3. Rebuild and restart:
```bash
docker-compose up -d --build
```

### Docker Compose Configuration

Both `docker-compose.yml` (build on server) and `docker-compose.registry.yml` (use registry image) include:
- Automatic Traefik routing with SSL certificates
- Volume mount for the Markdown file (read-only)
- Environment variable for the file path
- Automatic restarts

**Note**: The docker-compose files are configured to use the `traefik` network. If the network doesn't exist, create it with: `docker network create traefik`. If your Traefik uses a different network name, update the `networks` section in the docker-compose file.

### CI/CD Integration

You can automate building and pushing images using GitHub Actions, GitLab CI, or similar. Example GitHub Actions workflow:

```yaml
name: Build and Push Docker Image

on:
  push:
    branches: [ main ]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Login to Docker Hub
        uses: docker/login-action@v2
        with:
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}
      - name: Build and push
        uses: docker/build-push-action@v4
        with:
          push: true
          tags: YOUR_USERNAME/kardinal:latest
```

### Building the Image Manually

If you prefer to build and run without docker-compose:

```bash
# Build the image
docker build -t kardinal .

# Run the container
docker run -d \
  --name kardinal \
  --restart unless-stopped \
  -v /srv/kardinal/kardinal_public:/srv/kardinal/kardinal_public:ro \
  -e KANBAN_MARKDOWN_FILE="/srv/kardinal/kardinal_public/Grafana Labs Kanban.md" \
  --network traefik \
  --label "traefik.enable=true" \
  --label "traefik.http.routers.kanban.rule=Host(\`kardinal.nvdh.dev\`)" \
  --label "traefik.http.routers.kanban.entrypoints=websecure" \
  --label "traefik.http.routers.kanban.tls.certresolver=letsencrypt" \
  --label "traefik.http.services.kanban.loadbalancer.server.port=5000" \
  kardinal
```

## Manual Installation (Alternative)

If you prefer not to use Docker:

1. Clone this repository or copy files to your server

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

Or use a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

The Markdown file path can be configured via environment variable:

```bash
export KANBAN_MARKDOWN_FILE="/srv/kardinal/kardinal_public/Grafana Labs Kanban.md"
```

Default path: `/srv/kardinal/kardinal_public/Grafana Labs Kanban.md`

## Running the Application

### Development Mode

```bash
python app.py
```

The application will run on `http://127.0.0.1:5000`

### Production with Gunicorn (Recommended)

Install Gunicorn:
```bash
pip install gunicorn
```

Run with Gunicorn:
```bash
gunicorn -w 2 -b 127.0.0.1:5000 app:app
```

### Systemd Service

Create a systemd service file at `/etc/systemd/system/kardinal.service`:

```ini
[Unit]
Description=Kanban Board Flask Application
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/kardinal
Environment="PATH=/path/to/kardinal/venv/bin"
Environment="KANBAN_MARKDOWN_FILE=/srv/kardinal/kardinal_public/Grafana Labs Kanban.md"
ExecStart=/path/to/kardinal/venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Replace `/path/to/kardinal` with your actual deployment path.

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable kardinal
sudo systemctl start kardinal
```

Check status:
```bash
sudo systemctl status kardinal
```

## Traefik Configuration

### Setting Up Traefik

If Traefik isn't running yet, you can use the provided `traefik-docker-compose.yml` as a reference:

1. **Create Traefik directory and copy the compose file:**
```bash
mkdir -p /srv/traefik
# Copy traefik-docker-compose.yml to /srv/traefik/docker-compose.yml
# Or download it:
curl -o /srv/traefik/docker-compose.yml https://raw.githubusercontent.com/nicolevanderhoeven/kardinal/main/traefik-docker-compose.yml
```

2. **Create required directories:**
```bash
mkdir -p /srv/traefik/letsencrypt
mkdir -p /srv/traefik/dynamic
chmod 600 /srv/traefik/letsencrypt  # Secure the ACME storage
```

3. **Create the traefik network:**
```bash
docker network create traefik
```

4. **Start Traefik:**
```bash
cd /srv/traefik
docker-compose up -d
```

**Note:** Update the email in the compose file (`nicole@nicolevanderhoeven.com`) to your actual email for Let's Encrypt notifications.

### Docker Deployment (Recommended)

If using Docker with `docker-compose.yml`, Traefik labels are already configured. Just ensure:
- Traefik is running and can access the `traefik` network
- The `traefik` network exists (create it with `docker network create traefik` if needed)
- Traefik has Docker provider enabled (see `traefik-docker-compose.yml` for reference)

The labels in `docker-compose.yml` handle routing automatically.

### File-based Configuration (Non-Docker)

Add to your Traefik configuration file (typically `traefik.yml` or `/etc/traefik/traefik.yml`):

```yaml
http:
  routers:
    kanban:
      rule: "Host(`kardinal.nvdh.dev`)"
      entryPoints:
        - websecure
      service: kanban
      tls:
        certResolver: letsencrypt
  services:
    kanban:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:5000"
```

After updating Traefik configuration, reload Traefik:
```bash
sudo systemctl reload traefik
```

## File Permissions

### Docker Deployment

The Docker container mounts the Markdown file directory as a read-only volume. Ensure the file is readable:

```bash
sudo chmod 644 "/srv/kardinal/kardinal_public/Grafana Labs Kanban.md"
```

The container runs as a non-root user, so ensure the file has appropriate permissions for group/other read access.

### Manual Installation

Ensure the application user (e.g., `www-data`) has read access to the Markdown file:

```bash
sudo chmod 644 "/srv/kardinal/kardinal_public/Grafana Labs Kanban.md"
sudo chown www-data:www-data "/srv/kardinal/kardinal_public/Grafana Labs Kanban.md"
```

Or if the file is in a Syncthing directory, ensure the service user can read it.

## Troubleshooting

- **File not found**: 
  - Docker: Check that the volume mount path is correct and the file exists on the host
  - Manual: Check that the Markdown file path is correct and the application user has read permissions
- **No columns displayed**: Verify the Markdown file uses `##` for column headers and `- [ ]` for cards
- **Traefik not routing**: 
  - Docker: Check that the container is on the Traefik network and labels are correct. View logs: `docker logs kardinal`
  - Manual: Check Traefik logs and ensure the service is running on port 5000
- **Container won't start**: Check Docker logs: `docker logs kardinal` or `docker-compose logs`
- **Network issues**: Ensure the `traefik` network exists: `docker network ls | grep traefik`. If it doesn't exist, create it: `docker network create traefik`. Then update your Traefik container to use this network.

## License

MIT

