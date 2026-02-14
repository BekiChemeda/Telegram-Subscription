# Deployment Guide

You have two main options for deploying this Telegram Subscription Bot.

## Option 1: VPS (DigitalOcean, Hetzner, AWS) - Recommended for Control
We have included a `docker-compose.yml` file which creates a full environment with Python and MongoDB.

### Steps:
1. **Get a VPS**: Buy a small Linux server (Ubuntu 22.04).
2. **Install Docker**:
   ```bash
   sudo apt update
   sudo apt install docker.io docker-compose
   ```
3. **Upload Code**: Clone this repo or copy files to the server.
4. **Configure Environment**:
   - Create a `.env` file on the server.
   - Set `MONGO_URI=mongodb://mongo:27017/telegram_sub_bot` (Note: `mongo` is the service name in docker-compose).
5. **Run**:
   ```bash
   sudo docker-compose up -d --build
   ```
6. **Webhooks**:
   - The server will listen on port 8080 (externally).
   - Set your Stripe Webhook URL to `http://<YOUR_VPS_IP>:8080/stripe/webhook`

## Option 2: PaaS (Railway, Render, Heroku) - Easiest
These platforms handle the servers for you.

### Steps:
1. **Push to GitHub**: Make sure your code is in a GitHub repo.
2. **Create Project**: Go to Railway.app or Render.com and create a new project from your repo.
3. **Database**:
   - **Railway**: Add a MongoDB plugin service.
   - **Render**: Create a free MongoDB Atlas account and get the connection string.
4. **Environment Variables**:
   - Copy your `.env` variables into the platform's dashboard.
   - Set `MONGO_URI` to the one provided by your cloud database.
5. **Start Command**:
   - The platform will detect the `Procfile` automatically.
   - It will start two services: `web` (for Stripe) and `worker` (for the Bot).

## Important Checks
- **Stripe Webhook**: Ensure your Stripe Dashboard has the correct live URL.
- **Bot Token**: Ensure you are using the correct Bot Token (Test vs Live).
