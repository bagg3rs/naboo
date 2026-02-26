"""Model routing for intelligent cost-based model selection."""

import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional, Any, TYPE_CHECKING
from enum import Enum

from .query_classifier import QueryComplexity

if TYPE_CHECKING:
    from .web_grounding_config import WebGroundingConfig

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for a model provider and model."""
    
    provider: str  # "bedrock", "openai", "anthropic", "gemini", "ollama"
    model_id: str
    cost_per_1k_input_tokens: float
    cost_per_1k_output_tokens: float
    max_tokens: int
    supports_streaming: bool
    supports_vision: bool
    region: Optional[str] = None  # For Bedrock models
    host: Optional[str] = None  # For Ollama models
    use_inference_profile: bool = False  # Whether to use Bedrock inference profile
    inference_profile_id: Optional[str] = None  # Inference profile ID (if different from model_id)
    
    def __post_init__(self):
        """Validate configuration."""
        if self.provider not in ["bedrock", "openai", "anthropic", "gemini", "ollama"]:
            raise ValueError(f"Invalid provider: {self.provider}")
        
        if self.cost_per_1k_input_tokens < 0 or self.cost_per_1k_output_tokens < 0:
            raise ValueError("Cost values must be non-negative")
        
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        
        # Validate inference profile settings
        if self.use_inference_profile and self.provider != "bedrock":
            raise ValueError("Inference profiles are only supported for Bedrock provider")
    
    def get_effective_model_id(self) -> str:
        """
        Get the effective model ID to use for API calls.
        
        Returns inference_profile_id if using inference profile and it's set,
        otherwise returns model_id.
        """
        if self.use_inference_profile and self.inference_profile_id:
            return self.inference_profile_id
        return self.model_id
    
    def get_access_method(self) -> str:
        """
        Get a human-readable description of the access method.
        
        Returns:
            String describing whether using inference profile or direct access
        """
        if self.provider == "bedrock" and self.use_inference_profile:
            return "inference_profile"
        elif self.provider == "bedrock":
            return "direct_model_access"
        else:
            return f"{self.provider}_direct"


class ModelRouter:
    """
    Route queries to cost-appropriate models based on complexity.
    
    The router selects models based on:
    - Query complexity (simple/moderate/complex/current_info)
    - Agent type (for per-agent overrides)
    - Model capabilities (vision support, streaming)
    - Cost optimization goals
    - Web grounding configuration (for Nova 2 models)
    """
    
    # Nova 2 model patterns for web grounding detection
    # Format: amazon.nova-2-{size}-v1:0 or global.amazon.nova-2-{size}-v1:0
    NOVA2_MODEL_PATTERNS = [
        "amazon.nova-2-micro",
        "amazon.nova-2-lite",
        "amazon.nova-2-pro",
    ]
    
    def __init__(
        self,
        model_configs: Dict[QueryComplexity, ModelConfig],
        agent_overrides: Optional[Dict[str, Dict[QueryComplexity, ModelConfig]]] = None,
        web_grounding_config: Optional["WebGroundingConfig"] = None,
    ):
        """
        Initialize model router with configurations.
        
        Args:
            model_configs: Default model configs for each complexity level
            agent_overrides: Optional per-agent model overrides
                            Format: {"agent_name": {QueryComplexity.SIMPLE: config, ...}}
            web_grounding_config: Optional configuration for Nova 2 web grounding
        """
        self.model_configs = model_configs
        self.agent_overrides = agent_overrides or {}
        self.web_grounding_config = web_grounding_config
        
        # Validate that we have configs for required complexity levels
        # CURRENT_INFO is optional - it's handled via web_grounding_config
        required_levels = {QueryComplexity.SIMPLE, QueryComplexity.MODERATE, QueryComplexity.COMPLEX}
        if not required_levels.issubset(set(model_configs.keys())):
            raise ValueError(f"Must provide configs for all complexity levels: {required_levels}")
        
        logger.info(f"Model router initialized with {len(model_configs)} default configs")
        if agent_overrides:
            logger.info(f"Agent overrides configured for: {list(agent_overrides.keys())}")
        if web_grounding_config and web_grounding_config.enabled:
            logger.info(
                f"Web grounding enabled with model: {web_grounding_config.model_id}"
            )
    
    def select_model(
        self,
        complexity: QueryComplexity,
        agent_type: Optional[str] = None,
        requires_vision: bool = False
    ) -> ModelConfig:
        """
        Select appropriate model based on complexity and agent type.
        
        Args:
            complexity: Query complexity level
            agent_type: Optional agent identifier for per-agent overrides
            requires_vision: Whether the query requires vision support
            
        Returns:
            ModelConfig for the selected model
            
        Raises:
            ValueError: If no suitable model found
        """
        # Handle CURRENT_INFO complexity - route to Nova 2 if web grounding enabled
        if complexity == QueryComplexity.CURRENT_INFO:
            if self.web_grounding_config and self.web_grounding_config.enabled:
                config = self._get_web_grounding_model()
                logger.info(
                    f"Selected Nova 2 model for current info query: "
                    f"{config.provider}/{config.model_id}"
                )
                return config
            else:
                # Fall back to MODERATE complexity when web grounding disabled
                logger.info(
                    "Web grounding disabled, falling back to MODERATE for current info query"
                )
                complexity = QueryComplexity.MODERATE
        
        # Check for agent-specific override first
        if agent_type and agent_type in self.agent_overrides:
            agent_configs = self.agent_overrides[agent_type]
            if complexity in agent_configs:
                config = agent_configs[complexity]
                
                # Verify vision support if required
                if requires_vision and not config.supports_vision:
                    logger.warning(
                        f"Agent override for {agent_type}/{complexity} doesn't support vision, "
                        f"falling back to default"
                    )
                else:
                    logger.info(
                        f"Using agent override for {agent_type}: "
                        f"{config.provider}/{config.model_id}"
                    )
                    return config
        
        # Use default config for complexity level
        config = self.model_configs[complexity]
        
        # Verify vision support if required
        if requires_vision and not config.supports_vision:
            # Try to find a vision-capable model at the same or higher complexity
            for fallback_complexity in [complexity, QueryComplexity.MODERATE, QueryComplexity.COMPLEX]:
                fallback_config = self.model_configs.get(fallback_complexity)
                if fallback_config and fallback_config.supports_vision:
                    logger.info(
                        f"Selected vision-capable fallback: "
                        f"{fallback_config.provider}/{fallback_config.model_id}"
                    )
                    return fallback_config
            
            raise ValueError(f"No vision-capable model available for complexity {complexity}")
        
        logger.info(
            f"Selected model for {complexity}: {config.provider}/{config.model_id}"
        )
        return config
    
    def _get_web_grounding_model(self) -> ModelConfig:
        """
        Get Nova 2 model config for web grounding.
        
        Creates a ModelConfig from the web grounding configuration.
        
        Returns:
            ModelConfig configured for Nova 2 web grounding
            
        Raises:
            ValueError: If web grounding config is not available
        """
        if not self.web_grounding_config:
            raise ValueError("Web grounding config not available")
        
        return ModelConfig(
            provider="bedrock",
            model_id=self.web_grounding_config.model_id,
            cost_per_1k_input_tokens=self.web_grounding_config.cost_per_1k_input,
            cost_per_1k_output_tokens=self.web_grounding_config.cost_per_1k_output,
            max_tokens=self.web_grounding_config.max_tokens,
            supports_streaming=True,
            supports_vision=self.web_grounding_config.supports_vision,
            region=self.web_grounding_config.region,
        )
    
    def is_nova2_model(self, model_id: str) -> bool:
        """
        Check if a model ID is a Nova 2 model with web grounding capability.
        
        Nova 2 models have built-in web grounding that can search the web
        for current information without requiring external tools.
        
        Args:
            model_id: Model ID to check
            
        Returns:
            True if model is a Nova 2 model, False otherwise
            
        Example:
            >>> router.is_nova2_model("amazon.nova-pro-v2:0")
            True
            >>> router.is_nova2_model("anthropic.claude-3-haiku")
            False
        """
        if not model_id:
            return False
        return any(pattern in model_id for pattern in self.NOVA2_MODEL_PATTERNS)
    
    def get_model_instance(self, config: ModelConfig) -> Any:
        """
        Create Strands model instance from config.
        
        Args:
            config: Model configuration
            
        Returns:
            Strands model instance (BedrockModel, OllamaModel, etc.)
            
        Raises:
            ImportError: If required Strands model class not available
            ValueError: If provider not supported
        """
        if config.provider == "bedrock":
            from strands.models import BedrockModel
            
            # Determine which model ID to use
            effective_model_id = config.get_effective_model_id()
            access_method = config.get_access_method()
            
            # Log the access method being used
            logger.info(
                f"Creating Bedrock model with {access_method}: {effective_model_id}"
            )
            
            try:
                # Try to create model with the effective ID (inference profile or direct)
                model = BedrockModel(
                    model_id=effective_model_id,
                    region_name=config.region or "us-east-1",
                    max_tokens=config.max_tokens,
                )
                
                logger.info(
                    f"Successfully created Bedrock model using {access_method}"
                )
                return model
                
            except Exception as e:
                # If using inference profile and it fails, fall back to direct model access
                if config.use_inference_profile:
                    logger.warning(
                        f"Failed to use inference profile {effective_model_id}: {e}. "
                        f"Falling back to direct model access: {config.model_id}"
                    )
                    
                    try:
                        model = BedrockModel(
                            model_id=config.model_id,
                            region_name=config.region or "us-east-1",
                            max_tokens=config.max_tokens,
                        )
                        
                        logger.info(
                            f"Successfully created Bedrock model using direct_model_access "
                            f"(fallback from inference profile)"
                        )
                        return model
                        
                    except Exception as fallback_error:
                        logger.error(
                            f"Failed to create Bedrock model even with fallback: {fallback_error}"
                        )
                        raise
                else:
                    # Not using inference profile, so just raise the original error
                    logger.error(f"Failed to create Bedrock model: {e}")
                    raise
        
        elif config.provider == "ollama":
            from strands.models.ollama import OllamaModel
            
            logger.info(
                f"Creating Ollama model: {config.model_id} at {config.host or 'http://localhost:11434'}"
            )
            
            return OllamaModel(
                host=config.host or "http://localhost:11434",
                model_id=config.model_id,
                max_tokens=config.max_tokens,
            )
        
        elif config.provider == "openai":
            from strands.models.openai import OpenAIModel
            
            logger.info(f"Creating OpenAI model: {config.model_id}")
            
            return OpenAIModel(
                model_id=config.model_id,
                max_tokens=config.max_tokens,
            )
        
        elif config.provider == "anthropic":
            from strands.models.anthropic import AnthropicModel
            
            logger.info(f"Creating Anthropic model: {config.model_id}")
            
            return AnthropicModel(
                model_id=config.model_id,
                max_tokens=config.max_tokens,
            )
        
        elif config.provider == "gemini":
            from strands.models.gemini import GeminiModel
            
            logger.info(f"Creating Gemini model: {config.model_id}")
            
            return GeminiModel(
                model_id=config.model_id,
                max_tokens=config.max_tokens,
            )
        
        else:
            raise ValueError(f"Unsupported provider: {config.provider}")
    
    def get_config_for_complexity(
        self,
        complexity: QueryComplexity,
        agent_type: Optional[str] = None
    ) -> ModelConfig:
        """
        Get model config for a complexity level (without vision requirement).
        
        This is a convenience method that wraps select_model with requires_vision=False.
        
        Args:
            complexity: Query complexity level
            agent_type: Optional agent identifier
            
        Returns:
            ModelConfig for the complexity level
        """
        return self.select_model(complexity, agent_type, requires_vision=False)
    
    def get_all_configs(self) -> Dict[QueryComplexity, ModelConfig]:
        """
        Get all default model configurations.
        
        Returns:
            Dictionary mapping complexity levels to model configs
        """
        return self.model_configs.copy()
    
    def get_agent_overrides(self, agent_type: str) -> Optional[Dict[QueryComplexity, ModelConfig]]:
        """
        Get agent-specific overrides if they exist.
        
        Args:
            agent_type: Agent identifier
            
        Returns:
            Dictionary of overrides or None if no overrides exist
        """
        return self.agent_overrides.get(agent_type)
    
    def add_agent_override(
        self,
        agent_type: str,
        complexity: QueryComplexity,
        config: ModelConfig
    ) -> None:
        """
        Add or update an agent-specific model override.
        
        Args:
            agent_type: Agent identifier
            complexity: Complexity level to override
            config: Model configuration to use
        """
        if agent_type not in self.agent_overrides:
            self.agent_overrides[agent_type] = {}
        
        self.agent_overrides[agent_type][complexity] = config
        logger.info(
            f"Added override for {agent_type}/{complexity}: "
            f"{config.provider}/{config.model_id}"
        )
    
    def remove_agent_override(
        self,
        agent_type: str,
        complexity: Optional[QueryComplexity] = None
    ) -> None:
        """
        Remove agent-specific overrides.
        
        Args:
            agent_type: Agent identifier
            complexity: Optional specific complexity level to remove.
                       If None, removes all overrides for the agent.
        """
        if agent_type not in self.agent_overrides:
            return
        
        if complexity is None:
            del self.agent_overrides[agent_type]
            logger.info(f"Removed all overrides for {agent_type}")
        else:
            if complexity in self.agent_overrides[agent_type]:
                del self.agent_overrides[agent_type][complexity]
                logger.info(f"Removed override for {agent_type}/{complexity}")
                
                # Clean up empty agent override dict
                if not self.agent_overrides[agent_type]:
                    del self.agent_overrides[agent_type]



def create_bedrock_config_from_env(
    model_id_env_var: str = "BEDROCK_MODEL_ID",
    region_env_var: str = "AWS_REGION",
    max_tokens_env_var: str = "BEDROCK_MAX_TOKENS",
    use_inference_profile_env_var: str = "USE_INFERENCE_PROFILE",
    inference_profile_id_env_var: str = "INFERENCE_PROFILE_ID",
    cost_per_1k_input: float = 0.80,
    cost_per_1k_output: float = 4.00,
    supports_streaming: bool = True,
    supports_vision: bool = False,
) -> ModelConfig:
    """
    Create a Bedrock ModelConfig from environment variables.
    
    This helper function reads configuration from environment variables and
    creates a ModelConfig with inference profile support.
    
    Args:
        model_id_env_var: Environment variable name for model ID
        region_env_var: Environment variable name for AWS region
        max_tokens_env_var: Environment variable name for max tokens
        use_inference_profile_env_var: Environment variable name for inference profile flag
        inference_profile_id_env_var: Environment variable name for inference profile ID
        cost_per_1k_input: Cost per 1K input tokens (default for Haiku 4.5)
        cost_per_1k_output: Cost per 1K output tokens (default for Haiku 4.5)
        supports_streaming: Whether the model supports streaming
        supports_vision: Whether the model supports vision
        
    Returns:
        ModelConfig configured from environment variables
        
    Example:
        # Using default environment variables
        config = create_bedrock_config_from_env()
        
        # Using custom environment variables
        config = create_bedrock_config_from_env(
            model_id_env_var="NABOO_MODEL_ID",
            cost_per_1k_input=3.00,
            cost_per_1k_output=15.00,
        )
    """
    model_id = os.getenv(model_id_env_var, "anthropic.claude-3-haiku-20240307-v1:0")
    region = os.getenv(region_env_var, "us-east-1")
    max_tokens = int(os.getenv(max_tokens_env_var, "500"))
    
    # Check if we should use inference profile
    use_inference_profile = os.getenv(use_inference_profile_env_var, "").lower() in ["true", "1", "yes"]
    inference_profile_id = os.getenv(inference_profile_id_env_var, "")
    
    # Auto-detect inference profile from model ID
    # Regional inference profiles have format: region.provider.model-name
    if not use_inference_profile and "." in model_id and model_id.count(".") >= 2:
        # This looks like an inference profile ID (e.g., eu.anthropic.claude-haiku-4-5-20251001-v1:0)
        parts = model_id.split(".")
        if len(parts) >= 3 and parts[0] in ["us", "eu", "ap"]:
            use_inference_profile = True
            logger.info(f"Auto-detected inference profile from model ID: {model_id}")
    
    # If using inference profile but no separate ID provided, use model_id as the profile ID
    if use_inference_profile and not inference_profile_id:
        inference_profile_id = model_id
        
        # Try to extract the base model ID from the inference profile ID
        # e.g., eu.anthropic.claude-haiku-4-5-20251001-v1:0 -> anthropic.claude-haiku-4-5-20251001-v1:0
        if "." in model_id and model_id.count(".") >= 2:
            parts = model_id.split(".", 1)
            if len(parts) == 2 and parts[0] in ["us", "eu", "ap"]:
                # This is a regional prefix, extract the base model ID
                base_model_id = parts[1]
                logger.info(
                    f"Extracted base model ID from inference profile: {base_model_id}"
                )
                model_id = base_model_id
    
    return ModelConfig(
        provider="bedrock",
        model_id=model_id,
        cost_per_1k_input_tokens=cost_per_1k_input,
        cost_per_1k_output_tokens=cost_per_1k_output,
        max_tokens=max_tokens,
        supports_streaming=supports_streaming,
        supports_vision=supports_vision,
        region=region,
        use_inference_profile=use_inference_profile,
        inference_profile_id=inference_profile_id if inference_profile_id else None,
    )
