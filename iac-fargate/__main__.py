# Here we have fleshed out our infracstructure and added out a load
# balancer that we can add infrastructure to.

# Then we ran a container AWS FARGATE.
# FARGATE is a service that enables a user to run containers on amazon's cloud 
# computing PLATFORM without the need to manage the underlaying infracstructure.

import pulumi
import pulumi_aws as aws
import json


# --- ECS Cluster Setup ---
# 1. Create the base ECS Cluster resource.
# This serves as the logical grouping for the Fargate services.
cluster = aws.ecs.Cluster("cluster")


# --- Networking Lookups ---
# 2. Look up the default VPC for the current AWS region.
# This assumes a default VPC exists and is used for networking resources.
vpc = aws.ec2.get_vpc(default=True)

# 3. Retrieve the IDs of all subnets within the default VPC.
# These will be used for the ALB and the ECS Fargate Service.
# Note: The result object has an 'ids' property which holds the list of strings.
vpc_subnets = aws.ec2.get_subnets(
    filters=[
        {
            "name": "vpc-id",
            "values": [vpc.id],
        }
    ]
)


# --- Load Balancer Security Group ---
# 4. Create a Security Group to control traffic to the ALB and ECS tasks.
group = aws.ec2.SecurityGroup(
    "web-secgrp",
    vpc_id=vpc.id,
    description="Enable HTTP access",
    ingress=[
        # Ingress Rule 1: Allows ping (ICMP type 8) for network testing from anywhere (0.0.0.0/0).
        {
            "protocol": "icmp",
            "from_port": 8,
            "to_port": 0,
            "cidr_blocks": ["0.0.0.0/0"],
        },
        # Ingress Rule 2: Allows HTTP traffic on port 80 from anywhere (0.0.0.0/0).
        {
            "protocol": "tcp",
            "from_port": 80,
            "to_port": 80,
            "cidr_blocks": ["0.0.0.0/0"],
        },
    ],
    egress=[
        # Egress Rule: Allows all outbound traffic (protocol -1) to anywhere (0.0.0.0/0).
        {
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": ["0.0.0.0/0"],
        }
    ],
)


# --- Application Load Balancer (ALB) ---
# 5. Create an Application Load Balancer (ALB).
# It's set as 'internet-facing' and placed in all available subnets.
alb = aws.lb.LoadBalancer(
    "app-lb",
    # Set to "false" to make it internet-facing (not internal).
    internal=False, 
    # Assign the Security Group created above.
    security_groups=[group.id],
    # Place the ALB in all subnets found in the default VPC.
    subnets=vpc_subnets.ids,
    load_balancer_type="application",
)


# 6. Create a Target Group (ATG).
# This group holds the IP addresses of the ECS tasks (target_type="ip").
atg = aws.lb.TargetGroup(
    "app-tg",
    port=80,
    # Shorten the deregistration delay for faster updates/testing.
    deregistration_delay=0,
    protocol="HTTP",
    # Use 'ip' as the target type for Fargate services (awsvpc network mode).
    target_type="ip", 
    vpc_id=vpc.id,
)


# 7. Create an ALB Listener.
# This listens for traffic on port 80 and forwards it to the Target Group.
wl = aws.lb.Listener(
    "web",
    load_balancer_arn=alb.arn,
    port=80,
    default_actions=[{"type": "forward", "target_group_arn": atg.arn}],
)


# --- ECS Task Execution Role (IAM) ---
# 8. Create an IAM Role for the ECS Task Execution.
# This role grants permissions to ECS to pull images, log, and manage network resources.
role = aws.iam.Role("task-exec-role",
    assume_role_policy=json.dumps({
        "Version": "2008-10-17",
        "Statement": [{
            "Sid": "",
            "Effect": "Allow",
            "Principal": {
                "Service": "ecs-tasks.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }]
    }))


# 9. Attach the necessary managed policy to the execution role.
# This policy contains the actual permissions (e.g., ECR access, CloudWatch logs).
rpa = aws.iam.RolePolicyAttachment("task-exec-policy",
    role=role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
)


# --- ECS Task Definition ---
# 10. Define the ECS Task Definition.
# This specifies the resource limits, network mode, and container details for the service.
task_definition = aws.ecs.TaskDefinition("app-task",
    family="fargate-task-definition",
    cpu="256", # CPU units (e.g., 0.25 vCPUs)
    memory="512", # Memory in MiB
    network_mode="awsvpc", # Required for Fargate
    requires_compatibilities=["FARGATE"],
    execution_role_arn=role.arn,
    container_definitions=json.dumps([{
        "name": "my-app",
        # Use the public NGINX image
        "image": "nginx", 
        "portMappings": [{
            "containerPort": 80, # The port exposed by the container
            "hostPort": 80,      # The port on the host (ignored by Fargate, but used for mapping)
            "protocol": "tcp"
        }]
    }])
)


# --- ECS Service ---
# 11. Create the ECS Service.
# The service maintains the desired count of tasks and registers them with the ALB.
service = aws.ecs.Service("app-svc",
    cluster=cluster.arn,
    desired_count=3,
    launch_type="FARGATE",
    task_definition=task_definition.arn,
    network_configuration={
        # Assigns a public IP for internet access (if required).
        "assign_public_ip": "true", 
        # Places the task in the subnets.
        "subnets": vpc_subnets.ids, 
        # Applies the Security Group.
        "security_groups": [group.id] 
    },
    load_balancers=[{
        # Connects the service to the Target Group created earlier.
        "target_group_arn": atg.arn,
        "container_name": "my-app",
        "container_port": 80
    }],
    # Ensure the ALB Listener is ready before attempting to register the service.
    opts=pulumi.ResourceOptions(depends_on=[wl]) 
)


# --- Output ---
# 12. Export the DNS name of the Application Load Balancer.
# This provides the public URL to access the deployed NGINX service.
# This provision the cluster 'ECS' and the deployed NGINX service
pulumi.export("url", alb.dns_name)