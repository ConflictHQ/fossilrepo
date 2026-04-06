# Fossil SCM-based GitHub/GitLab-like Platform

This project aims to create a GitHub/GitLab-like platform based on Fossil SCM, providing a comprehensive solution for repository management, issue tracking, wikis, user management, and repository analytics.

## Core Features

- Version Control: Utilizes Fossil SCM for repository management
- Backend: Flask-based API for interacting with Fossil SCM and managing platform features
- Database: Supports both MySQL and PostgreSQL via feature flags
- Frontend: React-based web interface for viewing repositories, commits, issues, wikis, and user management
- Authentication & Permissions: OAuth (Google, GitHub) and custom JWT-based authentication
- CI/CD Integration: Optional continuous integration (feature flag enabled)
- Notification System: Email notifications and real-time WebSocket updates
- Extensibility: Plugin system for additional features

## Technical Stack

- Backend: Flask (Python)
- Frontend: React (JavaScript)
- Database: MySQL/PostgreSQL (configurable via feature flags)
- ORM: SQLAlchemy
- Authentication: OAuth 2.0, JWT
- Real-time Updates: WebSockets
- CI/CD: (Optional, configurable)

## Feature Flags

The platform uses feature flags to enable/disable certain functionalities:

- `DB_TYPE`: Toggle between MySQL and PostgreSQL (e.g., `DB_TYPE=mysql` or `DB_TYPE=postgres`)
- `ENABLE_CICD`: Enable/disable CI/CD integration (e.g., `ENABLE_CICD=true`)
- `ENABLE_NOTIFICATIONS`: Enable/disable real-time WebSocket updates and email notifications (e.g., `ENABLE_NOTIFICATIONS=true`)
- `AUTH_TYPE`: Choose between OAuth-based login or JWT (e.g., `AUTH_TYPE=oauth` or `AUTH_TYPE=jwt`)

## Getting Started

1. Clone the repository
2. Set up the backend:
   - Install Python dependencies: `pip install -r requirements.txt`
   - Configure environment variables for feature flags
   - Run the Flask server: `python app.py`
3. Set up the frontend:
   - Navigate to the frontend directory: `cd frontend`
   - Install npm packages: `npm install`
   - Start the React app: `npm start`

## Development Roadmap

1. Set up Fossil SCM integration
2. Implement database abstraction with SQLAlchemy
3. Develop core Flask backend API
4. Create basic React frontend
5. Implement authentication and authorization
6. Add notification system
7. Develop plugin system for extensibility
8. Implement CI/CD integration
9. Comprehensive testing and documentation

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests.

## License

This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details.
