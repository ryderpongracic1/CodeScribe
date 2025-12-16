# Deployment Guide for CodeScribe AI

## Quick Start - Deploy to Railway (Recommended)

Railway is the easiest platform to deploy this application. Follow these steps:

### 1. Prepare Your Repository

Make sure all your changes are committed to GitHub:

```bash
git add .
git commit -m "Add deployment configuration"
git push origin main
```

### 2. Deploy to Railway

1. Go to [Railway.app](https://railway.app/) and sign up/login with GitHub
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Choose your **CodeScribe** repository
5. Railway will auto-detect the configuration and start building

### 3. Configure Environment Variables

In your Railway project dashboard:

1. Click on your service
2. Go to **Variables** tab
3. Add the following environment variables:

```
GROQ_API_KEY_1=your_groq_api_key_here
GEMINI_API_KEY_1=your_gemini_api_key_here
GITHUB_CLIENT_ID=your_github_client_id
GITHUB_CLIENT_SECRET=your_github_client_secret
PORT=8000
```

4. Click **"Deploy"** to redeploy with the new variables

### 4. Update GitHub OAuth Callback URL

1. Go to your GitHub OAuth app settings: https://github.com/settings/developers
2. Find your OAuth App
3. Update the **Authorization callback URL** to:
   ```
   https://your-railway-app.up.railway.app/auth/github/callback
   ```
   (Replace `your-railway-app` with your actual Railway domain)
4. Save changes

### 5. Access Your Deployed App

Your app will be available at: `https://your-railway-app.up.railway.app`

Railway provides a free subdomain automatically!

---

## Alternative: Deploy to Render

### 1. Create a Render Account

Go to [Render.com](https://render.com/) and sign up with GitHub

### 2. Create New Web Service

1. Click **"New +"** → **"Web Service"**
2. Connect your CodeScribe repository
3. Configure the service:
   - **Name**: `codescribe-ai`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn server.main:app --host 0.0.0.0 --port $PORT`

### 3. Add Environment Variables

In the Environment tab, add:

```
GROQ_API_KEY_1=your_groq_api_key_here
GEMINI_API_KEY_1=your_gemini_api_key_here
GITHUB_CLIENT_ID=your_github_client_id
GITHUB_CLIENT_SECRET=your_github_client_secret
```

### 4. Deploy

Click **"Create Web Service"** and Render will automatically deploy your app

### 5. Update GitHub OAuth

Update your OAuth callback URL to: `https://your-app.onrender.com/auth/github/callback`

---

## Alternative: Deploy to Fly.io

### 1. Install Fly CLI

```bash
curl -L https://fly.io/install.sh | sh
```

### 2. Login and Initialize

```bash
fly auth login
fly launch
```

### 3. Set Secrets

```bash
fly secrets set GROQ_API_KEY_1="your_groq_api_key_here"
fly secrets set GEMINI_API_KEY_1="your_gemini_api_key_here"
fly secrets set GITHUB_CLIENT_ID="your_github_client_id"
fly secrets set GITHUB_CLIENT_SECRET="your_github_client_secret"
```

### 4. Deploy

```bash
fly deploy
```

---

## Local Development

To run locally:

```bash
# Install dependencies
pip install -r requirements.txt

# Create .env file with your API keys (already done)
# Run the server
python3 run.py
```

Visit: http://localhost:8000

---

## Troubleshooting

### Issue: "GitHub OAuth not working"

**Solution**: Make sure your GitHub OAuth callback URL matches your deployed domain exactly.

### Issue: "Port binding error"

**Solution**: Railway/Render automatically set the `PORT` environment variable. Make sure you're using `--port $PORT` in your start command.

### Issue: "LLM API errors"

**Solution**:
- Verify your API keys are correct in environment variables
- Check if you have rate limits on your Groq/Gemini accounts
- You can add multiple keys: `GROQ_API_KEY_2`, `GROQ_API_KEY_3`, etc.

### Issue: "Static files not loading"

**Solution**: Make sure the `static/` directory is included in your repository and not in `.gitignore`.

---

## Security Notes

⚠️ **IMPORTANT**: The API keys shown in this guide should be rotated immediately after deployment. Never commit API keys to public repositories!

To rotate your keys:
1. Generate new API keys from Groq/Gemini/GitHub
2. Update environment variables in Railway/Render dashboard
3. Redeploy the service

---

## Custom Domain (Optional)

### Railway
1. Go to Settings → Domains
2. Add your custom domain
3. Update DNS records as instructed
4. Update GitHub OAuth callback URL

### Render
1. Go to Settings → Custom Domains
2. Follow the DNS configuration steps
3. Update GitHub OAuth callback URL

---

## Monitoring

- **Railway**: Built-in logs and metrics in the dashboard
- **Render**: Real-time logs available in the Logs tab
- **Fly.io**: Use `fly logs` command

---

## Scaling

For production use with high traffic:

1. **Upgrade your plan** on Railway/Render for better resources
2. **Add more API keys** to handle rate limits
3. **Enable auto-scaling** in your platform settings
4. **Monitor usage** and set up alerts

---

## Support

For issues or questions:
- Check the [GitHub Issues](https://github.com/your-username/CodeScribe/issues)
- Review the [README.md](README.md)
- Contact the maintainer

---

**Last Updated**: December 2025
