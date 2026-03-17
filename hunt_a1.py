#!/usr/bin/env python3
"""
hunt_a1.py — Oracle Cloud A1.Flex availability hunter.

Polls OCI every N minutes across all availability domains and attempts to
create an A1.Flex instance the moment capacity opens. Sends an email
notification (and logs loudly) on success, then exits.

Usage:
    python hunt_a1.py

    # Run in background on your E2.1.Micro (survives SSH disconnect):
    nohup python hunt_a1.py >> hunt_a1.log 2>&1 &

    # Follow the log:
    tail -f hunt_a1.log

Configuration:
    Copy .env.hunt.example to .env.hunt and fill in your values.
    See inline comments for where to find each OCID in the Console.

Requirements:
    pip install oci python-dotenv
    (separate from the main app — don't add to requirements.txt)

OCI one-time setup (before running):
    1. Oracle Cloud Console → Identity & Security → Users → your user
       → API Keys → Add API Key
       Download the private key (.pem), note the fingerprint shown.
    2. Create a VCN: Networking → Virtual Cloud Networks → Create VCN
       Use "Create VCN with Internet Connectivity" wizard — creates subnet too.
    3. Find your subnet OCID: VCN → Subnets → click subnet → copy OCID.
    4. Find ARM image OCID: run the helper once:
           python hunt_a1.py --list-images
"""

import os
import sys
import time
import logging
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Load hunt-specific env file (separate from main .env so secrets stay isolated)
load_dotenv(Path(__file__).parent / ".env.hunt")

try:
    import oci
except ImportError:
    print("ERROR: 'oci' package not installed. Run:  pip install oci")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hunt_a1")


# ---------------------------------------------------------------------------
# Configuration from .env.hunt
# ---------------------------------------------------------------------------

def _require(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        logger.error("Missing required config: %s — check .env.hunt", key)
        sys.exit(1)
    return val


TENANCY_OCID     = _require("OCI_TENANCY_OCID")
USER_OCID        = _require("OCI_USER_OCID")
FINGERPRINT      = _require("OCI_FINGERPRINT")
KEY_FILE         = _require("OCI_KEY_FILE")           # path to oci_api_key.pem
REGION           = os.environ.get("OCI_REGION", "us-phoenix-1")
COMPARTMENT_OCID = _require("OCI_COMPARTMENT_OCID")
SUBNET_OCID      = _require("OCI_SUBNET_OCID")
SSH_PUBLIC_KEY   = _require("OCI_SSH_PUBLIC_KEY")     # full "ssh-rsa AAAA..." string
IMAGE_OCID       = _require("OCI_IMAGE_OCID")         # Ubuntu 22.04 aarch64 in your region

# A1.Flex sizing — 1 large (4 OCPU / 24GB) recommended
OCPUS            = float(os.environ.get("OCI_A1_OCPUS",  "4"))
MEMORY_GB        = float(os.environ.get("OCI_A1_MEMORY", "24"))
BOOT_VOL_GB      = int(os.environ.get("OCI_BOOT_VOL_GB", "50"))
INSTANCE_NAME    = os.environ.get("OCI_INSTANCE_NAME", "vaultic-a1")

# Poll interval in minutes — 5 is polite; don't go below 2
POLL_MINUTES     = int(os.environ.get("POLL_MINUTES", "5"))

# Optional email notification when instance is created
NOTIFY_EMAIL     = os.environ.get("NOTIFY_EMAIL", "")
SMTP_FROM        = os.environ.get("SMTP_FROM", "")
SMTP_PASSWORD    = os.environ.get("SMTP_PASSWORD", "")   # Gmail: use App Password
SMTP_HOST        = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT        = int(os.environ.get("SMTP_PORT", "587"))


# ---------------------------------------------------------------------------
# OCI helpers
# ---------------------------------------------------------------------------

def _oci_config() -> dict:
    """Build OCI SDK config dict from env vars."""
    return {
        "user":        USER_OCID,
        "key_file":    KEY_FILE,
        "fingerprint": FINGERPRINT,
        "tenancy":     TENANCY_OCID,
        "region":      REGION,
    }


def _get_availability_domains() -> list[str]:
    """Return all AD names for the tenancy in the configured region."""
    identity = oci.identity.IdentityClient(_oci_config())
    ads = identity.list_availability_domains(TENANCY_OCID).data
    return [ad.name for ad in ads]


def list_arm_images():
    """
    Helper mode: print all Ubuntu 22.04 aarch64 image OCIDs in your region.
    Run with:  python hunt_a1.py --list-images
    Copy the most recent image OCID into OCI_IMAGE_OCID in .env.hunt.
    """
    compute = oci.core.ComputeClient(_oci_config())
    images = oci.pagination.list_call_get_all_results(
        compute.list_images,
        compartment_id=TENANCY_OCID,
        operating_system="Canonical Ubuntu",
        shape="VM.Standard.A1.Flex",
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data

    print(f"\nUbuntu images for VM.Standard.A1.Flex in {REGION}:\n")
    for img in images[:10]:
        print(f"  {img.display_name}")
        print(f"  OCID: {img.id}")
        print(f"  Created: {img.time_created}\n")


# ---------------------------------------------------------------------------
# Instance launch
# ---------------------------------------------------------------------------

def _attempt_launch(ad_name: str):
    """
    Try to launch an A1.Flex instance in the given availability domain.
    Returns the Instance object on success, None if out of capacity.
    Raises on any other error.
    """
    compute = oci.core.ComputeClient(_oci_config())

    launch_details = oci.core.models.LaunchInstanceDetails(
        availability_domain=ad_name,
        compartment_id=COMPARTMENT_OCID,
        display_name=INSTANCE_NAME,
        shape="VM.Standard.A1.Flex",
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=OCPUS,
            memory_in_gbs=MEMORY_GB,
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            source_type="image",
            image_id=IMAGE_OCID,
            boot_volume_size_in_gbs=BOOT_VOL_GB,
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=SUBNET_OCID,
            assign_public_ip=True,
        ),
        metadata={
            "ssh_authorized_keys": SSH_PUBLIC_KEY,
        },
    )

    try:
        response = compute.launch_instance(launch_details)
        return response.data

    except oci.exceptions.ServiceError as e:
        # Out of capacity comes back as HTTP 500 with a specific message
        msg = (e.message or "").lower()
        if e.status == 500 and ("out of host capacity" in msg or "out of capacity" in msg):
            return None  # expected — retry later
        raise  # unexpected error — let the caller log it


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def _notify_success(instance):
    """Log success loudly and optionally send an email."""
    summary = (
        f"A1.Flex instance created successfully!\n\n"
        f"  Name:    {instance.display_name}\n"
        f"  OCID:    {instance.id}\n"
        f"  AD:      {instance.availability_domain}\n"
        f"  Region:  {REGION}\n"
        f"  Shape:   {int(OCPUS)} OCPU / {int(MEMORY_GB)}GB RAM\n"
        f"  State:   {instance.lifecycle_state}\n\n"
        f"Next steps:\n"
        f"  1. Go to Oracle Cloud Console → Compute → Instances\n"
        f"  2. Click the instance to get its public IP address\n"
        f"  3. SSH in:  ssh -i ~/.ssh/id_rsa ubuntu@<public-ip>\n"
        f"  4. Run your server setup script\n"
    )

    logger.info("")
    logger.info("=" * 60)
    logger.info("SUCCESS! %s", summary)
    logger.info("=" * 60)
    logger.info("")

    if NOTIFY_EMAIL and SMTP_FROM and SMTP_PASSWORD:
        try:
            msg = MIMEText(summary)
            msg["Subject"] = "Oracle A1.Flex is YOURS — go grab the IP!"
            msg["From"]    = SMTP_FROM
            msg["To"]      = NOTIFY_EMAIL
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_FROM, SMTP_PASSWORD)
                server.send_message(msg)
            logger.info("Email notification sent to %s", NOTIFY_EMAIL)
        except Exception as e:
            logger.warning("Email failed (but instance WAS created!): %s", e)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    logger.info("A1.Flex hunter starting")
    logger.info("  Region:    %s", REGION)
    logger.info("  Shape:     VM.Standard.A1.Flex  %g OCPU / %gGB RAM", OCPUS, MEMORY_GB)
    logger.info("  Name:      %s", INSTANCE_NAME)
    logger.info("  Poll:      every %d minutes", POLL_MINUTES)
    logger.info("  Notify:    %s", NOTIFY_EMAIL or "email not configured")
    logger.info("")

    try:
        ads = _get_availability_domains()
    except Exception as e:
        logger.error("Failed to list availability domains: %s", e)
        logger.error("Check OCI credentials in .env.hunt")
        sys.exit(1)

    logger.info("Availability domains in %s: %s", REGION, ads)
    logger.info("")

    attempt = 0
    while True:
        attempt += 1
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("Attempt #%d  (%s)", attempt, ts)

        for ad in ads:
            try:
                logger.info("  Trying %s ...", ad)
                instance = _attempt_launch(ad)
                if instance:
                    _notify_success(instance)
                    sys.exit(0)  # done — instance was created
                else:
                    logger.info("  → Out of capacity")
            except oci.exceptions.ServiceError as e:
                logger.warning("  → OCI error (status=%s): %s", e.status, e.message)
            except Exception as e:
                logger.warning("  → Unexpected error: %s", e)

        logger.info("All ADs full. Waiting %d min...\n", POLL_MINUTES)
        time.sleep(POLL_MINUTES * 60)


if __name__ == "__main__":
    if "--list-images" in sys.argv:
        list_arm_images()
    else:
        main()
