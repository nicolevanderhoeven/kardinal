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

4. **Set up Traefik routing** (see "File-based Configuration" section below):
```bash
curl -o /srv/traefik/dynamic/kardinal.yml https://raw.githubusercontent.com/nicolevanderhoeven/kardinal/main/kardinal-traefik-config.yml
```

#### Updating the Application

**Option A: Automatic Updates with Watchtower (Recommended)**

Watchtower automatically monitors and updates your containers. See the "Automatic Updates with Watchtower" section below for setup instructions.

**Option B: Manual Updates**

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

3. **Set up Traefik routing** (see "File-based Configuration" section below):
```bash
curl -o /srv/traefik/dynamic/kardinal.yml https://raw.githubusercontent.com/nicolevanderhoeven/kardinal/main/kardinal-traefik-config.yml
```

4. The application will be available at `kardinal.nvdh.dev` (Traefik will automatically detect the config file)

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
- Volume mount for the Markdown file (read-only)
- Environment variable for the file path
- Automatic restarts
- Network configuration for Traefik

**Note**: 
- The docker-compose files are configured to use the `traefik` network. If the network doesn't exist, create it with: `docker network create traefik`.
- Traefik routing is configured via file-based configuration (see "File-based Configuration" section above), not Docker labels.
- After starting the kardinal container, make sure to set up the Traefik routing configuration file.

### Automatic Updates with Watchtower

Watchtower is an open-source tool that automatically monitors and updates your Docker containers. When combined with CI/CD (like GitHub Actions), you get fully automated deployments:

1. **Push code** → GitHub Actions builds and pushes new image
2. **Watchtower detects** the new image and automatically updates the container

#### Setting Up Watchtower

1. **On your server, start Watchtower**:
```bash
cd /path/to/kardinal
docker-compose -f docker-compose.watchtower.yml up -d
```

2. **Verify it's running**:
```bash
docker logs watchtower
```

#### Configuration

The Watchtower configuration (`docker-compose.watchtower.yml`) is set to:
- **Monitor only containers with the label** `com.centurylinklabs.watchtower.enable=true` (Kardinal has this label)
- **Check for updates every 5 minutes** (300 seconds)
- **Automatically clean up old images** after updating

#### Customizing the Update Schedule

To change how often Watchtower checks for updates, edit `docker-compose.watchtower.yml`:

- **Every 30 minutes**: Change `--interval 300` to `--interval 1800`
- **Every 6 hours**: Change `--interval 300` to `--interval 21600`
- **Daily at 2 AM**: Replace `--interval 300` with `--schedule "0 0 2 * * *"`

#### Optional: Email Notifications

To receive email notifications when updates occur, uncomment and configure the notification environment variables in `docker-compose.watchtower.yml`.

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

**Important:** The Traefik configuration uses file-based routing (not Docker provider) due to Docker API version compatibility. This is the recommended approach for this setup.

### File-based Configuration (Current Setup)

The current setup uses file-based configuration for Traefik routing. After deploying kardinal, you need to create the routing configuration:

1. **Copy the configuration file to Traefik's dynamic config directory:**
```bash
# Copy kardinal-traefik-config.yml to /srv/traefik/dynamic/kardinal.yml
curl -o /srv/traefik/dynamic/kardinal.yml https://raw.githubusercontent.com/nicolevanderhoeven/kardinal/main/kardinal-traefik-config.yml
```

Or create `/srv/traefik/dynamic/kardinal.yml` manually with:

```yaml
http:
  routers:
    kardinal:
      rule: "Host(`kardinal.nvdh.dev`)"
      entryPoints:
        - websecure
      service: kardinal
      tls:
        certResolver: letsencrypt
  
  services:
    kardinal:
      loadBalancer:
        servers:
          - url: "http://kardinal:5000"
```

**Note:** This assumes the kardinal container is on the `traefik` network and accessible by its container name. If using a different network or setup, adjust the URL accordingly.

2. **Traefik will automatically pick up the file** (since `--providers.file.watch=true` is set). No restart needed, but you can verify:
```bash
docker logs traefik | grep -i kardinal
```

### Docker Provider (Not Currently Used)

The Docker provider is disabled in the Traefik configuration due to Docker API version compatibility issues. The docker-compose files include Traefik labels for reference, but they won't be automatically detected. If you need to use Docker provider in the future, you'll need to:
- Update Traefik to a version with compatible Docker client
- Re-enable the Docker provider in `traefik-docker-compose.yml`

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

- **Watchtower Docker API version errors**:
  - If you see errors like "client version 1.25 is too old. Minimum supported API version is 1.44", the Watchtower container needs to be updated:
  ```bash
  # Pull the latest Watchtower image
  docker pull containrrr/watchtower:latest
  
  # Restart Watchtower with the new image
  docker-compose -f docker-compose.watchtower.yml down
  docker-compose -f docker-compose.watchtower.yml up -d
  ```
  - The compose file uses `latest` which should support newer Docker APIs. If issues persist, check [Watchtower releases](https://github.com/containrrr/watchtower/releases) for a specific version tag.

- **File not found**: 
  - Docker: Check that the volume mount path is correct and the file exists on the host
  - Manual: Check that the Markdown file path is correct and the application user has read permissions
- **No columns displayed**: Verify the Markdown file uses `##` for column headers and `- [ ]` for cards
- **Traefik not routing**: 
  - Check that the file-based config exists: `cat /srv/traefik/dynamic/kardinal.yml`
  - Verify the kardinal container is on the traefik network: `docker network inspect traefik | grep kardinal`
  - Check Traefik logs: `docker logs traefik | grep -i kardinal`
  - Verify the container is running: `docker logs kardinal`
- **Container won't start**: Check Docker logs: `docker logs kardinal` or `docker-compose logs`
- **Network issues**: Ensure the `traefik` network exists: `docker network ls | grep traefik`. If it doesn't exist, create it: `docker network create traefik`. Both Traefik and kardinal containers must be on this network.
- **Docker API version errors**: These are expected and harmless - the Docker provider is disabled. Traefik uses file-based configuration instead.

## License

MIT

