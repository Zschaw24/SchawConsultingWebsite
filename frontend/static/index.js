document.addEventListener('DOMContentLoaded', () => {
    fetchInventoryListings();
});

// Fetch listings from the backend API
async function fetchInventoryListings() {
    const apiUrl = 'http://127.0.0.1:8000/listings'; // Absolute URL to avoid pending requests
    console.log('Fetching listings from:', apiUrl);

    try {
        const response = await fetch(apiUrl);
        console.log('Fetch response:', response);

        if (!response.ok) {
            throw new Error(`HTTP error! Status: ${response.status}`);
        }

        const data = await response.json();
        console.log('Data received:', data);

        const listings = Array.isArray(data.listings) ? data.listings : [];
        displayListings(listings);

    } catch (error) {
        console.error('Error fetching inventory listings:', error);
        displayError('Failed to load inventory listings. Please try again later.');
    }
}

// Render the listings on the page
function displayListings(listings) {
    const container = document.getElementById('listings-container');
    container.innerHTML = '';

    if (listings.length === 0) {
        container.innerHTML = '<p>No listings available.</p>';
        return;
    }

    listings.forEach(listing => {
        const listingDiv = document.createElement('div');
        listingDiv.className = 'listing';

        // Title
        const title = document.createElement('h2');
        title.textContent = listing['item-name'] || 'No title available';

        // Price
        const price = document.createElement('p');
        price.textContent = `Price: $${listing.price ?? 'N/A'}`;

        // Image
        const image = document.createElement('img');
        image.src = listing.image_url || '/static/placeholder.jpg';
        image.alt = listing['item-name'] || 'Product image';
        image.style.width = '150px'; // Optional: fix width

        // Append elements
        listingDiv.append(title, price, image);
        container.appendChild(listingDiv);
    });
}

// Display error messages
function displayError(message) {
    const errorDiv = document.getElementById('error-message');
    errorDiv.textContent = message;
    errorDiv.style.display = 'block';
}
