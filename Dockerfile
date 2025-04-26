FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt requirements.txt
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy application code
COPY . .

# Expose the port
EXPOSE 5000

# Run the Flask application with gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
