# Online deployment on Azure for Students (no credit card)

Same app and scripts as `DEPLOY_FREE_ORACLE.md`; only the account and server steps (Part A) differ. Do Part A below, then continue with
Parts B-E of the Oracle guide using the differences listed at the end of this file.
**Demo data only:** the server is on the public internet. Use fictional patients and public EEG files.
**This is credit, not a free tier:** Azure for Students gives about $100 for 12 months while you are a student. A 2 vCPU / 4 GB server costs roughly
$30 a month if it runs all the time (aggregator figure for a US region: check the Azure pricing calculator for your region). Stop the server when you do not need it.
**Untested:** these steps and scripts have never run on a real server. Portal screens change: if a screen differs, follow what Azure shows.

## Part A: account and server
### A1. Activate the student offer
1. Go to azure.microsoft.com/free/students and choose **Activate now**. Sign in with a Microsoft account (you may create one).
2. Verify that you are a student: Microsoft sends a code to your **university email** (the one ending in your college's domain). If your university
   domain is not recognised, Microsoft may ask for another way to prove enrolment.
3. When done, open **portal.azure.com**. The subscription is called **Azure for Students**. Look at **Cost Management + Billing** to see the credit left.

### A2. Create the server (virtual machine)
Portal, **Virtual machines, Create, Azure virtual machine**:
- Subscription: Azure for Students. Resource group: **Create new**, name `neurotrace-rg`.
- Name: `neurotrace-vm`.
- **Region:** student subscriptions are restricted to a few regions that differ per person. Use only the regions the dropdown offers.
- Image: **Ubuntu Server 24.04 LTS (x64 Gen2)**.
- **Size:** you need **2 vCPUs and 4 GB RAM or more** (for example `Standard_B2s`, `Standard_B2s_v2` or `Standard_B2as_v2`). Do not choose 1 GB sizes: processing will run out of memory.
  If you see "not available for subscription" or "RequestDisallowedByAzure", that size or region is blocked for your subscription: try another size or one of your other regions.
  (To see what is deployable in a region, open Cloud Shell in the portal and run:
  `az vm list-skus --location REGION --resource-type virtualMachines --size Standard_B2 --output table`
  and pick a size without restrictions.) If nothing works, contact Azure support or your university's Azure administrator.
- Authentication: **SSH public key**; username `azureuser`; key pair name `neurotrace-key`; **Generate new key pair**. The `.pem` private key downloads when you create the VM: keep it safe.
- Inbound ports: allow **SSH (22), HTTP (80), HTTPS (443)**.
- Disks: OS disk **Standard SSD**, enlarge to **64 GB** (30 GB fills up quickly with Docker images and uploaded EEG).
- Networking: create a new public IP and choose **Static** (so the address does not change when you stop and start the server; a static address may cost a few dollars a month).
- Review + create, then Create. Download the key when asked.
- Note the **public IP address** (VM overview page).

### A3. Protect your credit
- VM page, **Auto-shutdown**: switch on and set a time (for example 23:30) so a forgotten server does not burn credit.
- **Cost Management, Budgets**: add a budget with an email alert (for example $60).
- To stop paying for compute, use **Stop** in the portal and wait for the status "Stopped (deallocated)". A stopped-only (not deallocated) VM is still billed. The disk and the static IP still cost a little.
- Start it again when you need it. The site (and your DuckDNS name) works again once it is running.

### A4. Connect from Windows
The downloaded key must not be readable by other users, or `ssh` refuses it. In PowerShell (fix the path):
```powershell
icacls "$env:USERPROFILE\Downloads\neurotrace-key.pem" /inheritance:r /grant:r "$($env:USERNAME):R"
ssh -i "$env:USERPROFILE\Downloads\neurotrace-key.pem" azureuser@PUBLIC_IP
```

## Parts B-E: differences from the Oracle guide
- Part B (DuckDNS): identical. Use the Azure public IP.
- Part C: the user is `azureuser`, not `ubuntu`. Everything else (git clone, `bash deploy/setup_server.sh`, `make_env.sh`, `docker compose ... up -d --build`, `create_admin.sh`) is the same.
  The firewall part of `setup_server.sh` is only needed on Oracle: Azure uses its own network rules (the ports you allowed in A2) and the script's step is harmless there.
- Part E: the backup cron path is `/home/azureuser/neurotrace`; copy backups with `scp -i KEY -r azureuser@IP:neurotrace/backups/nb-backup-... .`.
  The "Oracle may reclaim idle servers" note does not apply; the "Auto-shutdown" setting from A3 is the equivalent to watch.
- Builds: Azure Ubuntu x64 means the standard (non-ARM) images, which all the project's images support.
