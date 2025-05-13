# ABR Video Streaming Platform

## Overview

Production-ready Adaptive Bitrate (ABR) video streaming platform deployed on Amazon EKS. This system processes uploaded videos into multiple quality renditions and delivers streaming content based on network conditions using HLS (HTTP Live Streaming) protocol.

## Architecture

### System Design

Distributed microservices architecture with event-driven communication patterns:

- **Upload Service**: Video ingestion with format validation and S3 storage integration
- **Transcoding Service**: FFmpeg-based multi-rendition processing using worker pattern
- **Delivery Service**: HLS segment distribution with master playlist serving

### Technical Stack

- **Container Orchestration**: Amazon EKS 
- **Message Queuing**: AWS SQS
- **Notification**: AWS SNS
- **Storage**: AWS S3 (Raw & Processed buckets)
- **Database**: MongoDB
- **Video Processing**: FFmpeg
- **CI/CD**: GitHub Actions
- **K8s Deployment Management**: Helm
- **Ingress Controller**: NGINX
- **Monitoring**: HPA with custom metrics

## Service Architecture
![Image](https://github.com/user-attachments/assets/1b96600b-1888-4ddc-aba2-f8b1e03175ed)

### Upload Service
Processes video uploads with comprehensive validation (MP4 format, metadata verification). Upon successful validation, stores raw video in S3 bucket and publishes transcoding job to SQS queue. The service runs in Kubernetes pods with resource limits and readiness probes, automatically scaling based on upload traffic.

### Transcoding Service
Worker-based architecture continuously polling SQS for transcoding jobs. Upon receiving a job:
1. Downloads video from raw S3 bucket
2. Creates multiple renditions (240p-1080p) using FFmpeg
3. Generates HLS segments, playlists, and master playlist files
4. Updates processing status in MongoDB in real-time
5. Uploads processed content to dedicated S3 bucket
6. Sends SNS email notifications
7. Publishes delivery job to downstream SQS queue

Kubernetes deployment features memory-optimized nodes, persistent volume claims for temporary processing, and custom HPA metrics based on SQS queue depth.

### Delivery Service
Polls delivery SQS queue for transcoding completion status. Once transcoding is marked as "ready", serves HLS segments to frontend applications. The master playlist enables automatic quality switching based on network conditions.

### Here is a UI with the support of HLS to stream the video adaptively 
[Frontend StreamForge GitHub Repo](https://github.com/Sarang095/Frontend-StreamForge/tree/master)

## EKS & Pipeline Architecture (HLD)
![Image](https://github.com/user-attachments/assets/6318f2de-858d-4414-9abd-a0139d159a0a)

## Continuous Deployment Pipeline

GitHub Actions CI/CD pipeline with OIDC integration for keyless AWS authentication:

1. **Trigger**: Push to main branch initiates automated deployment
2. **Authentication**: GitHub Actions uses OIDC tokens to assume AWS IAM roles through AWS's OIDC identity provider integration
3. **Container Build**: Docker images built with semantic versioning using metadata-action
4. **Image tagging**: Image tagging done via docker/metadata-action@v5 in GitHub Actions
5. **EKS Deployment**: Cluster provisioning with eksctl and node group configuration
6. **IRSA Setup**: Service accounts created with granular IAM role assumptions
7. **Trust Policy**: Pod-level SQS permissions configured via IRSA
8. **Helm Deployment**: Application manifests deployed with environment-specific values
9. **Health Verification**: Readiness and liveness probes confirm successful deployment

## Security & Access Control

### IAM Strategy
- **EKS Admin Role**: GitHub Actions OIDC integration with time-limited sessions
- **IRSA Implementation**: Fine-grained AWS permissions at pod level
- **SQS Full Access Role**: Transcoding service workers with queue-specific permissions
- **S3 Bucket Policies**: Service-specific read/write access segregation

### Reliability 
- **Pod Disruption Budgets**: Used PDBs to maintain the service availability 
- **Rolling Updates**: Used rolling updated stratergy for delivery and upload service
- **Recreate Strtergy**: Used this for transcoding service
- **Circuit Breaker**: Failure isolation between services
- **Retry Logic**: Exponential backoff for faliures

### Infrastructure
- **Node Affinity**: Compute-intensive workloads on optimized instances
- **Resource Quotas**: Proper CPU/memory allocation
- **Storage**: Doing In-memory storage for faster transcoding for transcoding pods



