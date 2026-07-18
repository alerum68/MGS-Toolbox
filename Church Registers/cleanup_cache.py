"""
Gemini API Cache Cleanup Utility.

This script connects to the Google GenAI API using a provided API key,
retrieves a list of all currently active context caches, and deletes them
to prevent unnecessary storage costs or clutter.

Environment variables:
    GEMINI_API_KEY: Must be set in the environment or a .env file.
"""

import os

from dotenv import load_dotenv
from google import genai

# Load environment variables from a .env file if present, overriding existing
# environment variables with the values from the file.
load_dotenv(override=True)

# Initialize the GenAI Client using the API key from the environment.
# Ensure that GEMINI_API_KEY is correctly set before running this script.
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def cleanup_all_caches() -> None:
    """
    Fetch all active caches from the Gemini API and delete them.

    This function attempts to retrieve a list of all context caches associated
    with the configured API key. It iterates through the list and deletes each
    cache individually. It provides console output to track the progress and
    reports any errors encountered during deletion.

    Raises:
        Exceptions from the google.genai client are caught and printed.
    """
    print("Fetching active caches...")
    try:
        # Retrieve an iterator of all active caches associated with the account
        caches = client.caches.list()

        deleted_count = 0
        
        # Iterate over the retrieved caches and attempt to delete each one
        for cache in caches:
            print(
                f"Deleting cache: {cache.name} "
                f"(Created: {cache.create_time})"
            )
            try:
                # Delete the specific cache using its resource name
                client.caches.delete(name=cache.name)
                deleted_count += 1
            except Exception as delete_error:
                # Catch and log errors for individual cache deletion failures
                # to allow the loop to continue with other caches.
                print(f"  [!] Failed to delete {cache.name}: {delete_error}")

        # Provide a summary of the cleanup operation
        if deleted_count == 0:
            print("No active caches found. You're all clear!")
        else:
            print(f"\nSuccessfully deleted {deleted_count} orphaned caches.")

    except Exception as fetch_error:
        # Catch and log errors related to fetching the initial list of caches,
        # such as authentication issues or network errors.
        print(f"Error fetching caches: {fetch_error}")


if __name__ == "__main__":
    # Execute the cleanup function when the script is run directly
    cleanup_all_caches()