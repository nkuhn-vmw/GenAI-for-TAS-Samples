package org.cloudfoundry.samples.music.config;

import io.pivotal.cfenv.core.CfCredentials;
import io.pivotal.cfenv.core.CfEnv;
import io.pivotal.cfenv.core.CfService;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.boot.autoconfigure.data.mongo.MongoDataAutoConfiguration;
import org.springframework.boot.autoconfigure.data.mongo.MongoRepositoriesAutoConfiguration;
import org.springframework.boot.autoconfigure.data.redis.RedisAutoConfiguration;
import org.springframework.boot.autoconfigure.data.redis.RedisRepositoriesAutoConfiguration;
import org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration;
import org.springframework.boot.autoconfigure.mongo.MongoAutoConfiguration;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.core.env.ConfigurableEnvironment;
import org.springframework.core.env.MapPropertySource;
import org.springframework.core.env.Profiles;
import org.springframework.core.env.PropertySource;
import org.springframework.util.StringUtils;

import org.springframework.cloud.bindings.Bindings;
import org.springframework.cloud.bindings.Binding;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.Stream;

public class SpringApplicationContextInitializer implements ApplicationContextInitializer<ConfigurableApplicationContext> {

    private static final Log logger = LogFactory.getLog(SpringApplicationContextInitializer.class);

    private static final Map<String, List<String>> profileNameToServiceTags = new HashMap<>();
    private static final Map<String, String> serviceTypesToProfileName = new HashMap<>();

    static {
        profileNameToServiceTags.put("mongodb", Collections.singletonList("mongodb"));
        profileNameToServiceTags.put("postgres", Collections.singletonList("postgres"));
        profileNameToServiceTags.put("mysql", Collections.singletonList("mysql"));
        profileNameToServiceTags.put("redis", Collections.singletonList("redis"));
        profileNameToServiceTags.put("oracle", Collections.singletonList("oracle"));
        profileNameToServiceTags.put("sqlserver", Collections.singletonList("sqlserver"));
        
        serviceTypesToProfileName.put("postgresql", "postgres");        
    }

    @Override
    public void initialize(ConfigurableApplicationContext applicationContext) {
        ConfigurableEnvironment appEnvironment = applicationContext.getEnvironment();

        validateActiveProfiles(appEnvironment);

        addCloudProfile(appEnvironment);

        excludeAutoConfiguration(appEnvironment);
    }

    private void addCloudProfile(ConfigurableEnvironment appEnvironment) {
        CfEnv cfEnv = new CfEnv();

        List<String> profiles = new ArrayList<>();

        List<CfService> services = cfEnv.findAllServices();
        List<String> serviceNames = services.stream()
                .map(CfService::getName)
                .collect(Collectors.toList());

        Bindings bindings = new Bindings();
        List<String> k8sServiceTypes = bindings.getBindings().stream()
            .map(Binding::getType)
            .collect(Collectors.toList());

        logger.info("Found services " + StringUtils.collectionToCommaDelimitedString(serviceNames));
        logger.info("Found k8s service types " + StringUtils.collectionToCommaDelimitedString(k8sServiceTypes));

        for (CfService service : services) {
            for (String profileKey : profileNameToServiceTags.keySet()) {
                if (service.getTags().containsAll(profileNameToServiceTags.get(profileKey))) {
                    profiles.add(profileKey);
                }
            }
        }

        for (String type : k8sServiceTypes) {
            if (serviceTypesToProfileName.get(type) != null) {
                profiles.add(serviceTypesToProfileName.get(type));
            }
        }

        if (profiles.size() > 1) {
            throw new IllegalStateException(
                    "Only one service of the following types may be bound to this application: " +
                            profileNameToServiceTags.values().toString() + ". " +
                            "These services are bound to the application: [" +
                            StringUtils.collectionToCommaDelimitedString(profiles) + "]");
        }

        List<CfService> llmServices = cfEnv.findServicesByTag("llm");
        if (!llmServices.isEmpty()) {
            logger.info("Setting service profile llm");
            appEnvironment.addActiveProfile("llm");
          
        }

        if (k8sServiceTypes.contains("genai")) {
           logger.info("Setting service profile llm");
           appEnvironment.addActiveProfile("llm");
            // TODO update Spring Cloud Bindings for AI to GenAI 0.6 multi-binding.  The following handles 0.4
             bindings.filterBindings("genai").forEach(binding -> {
			 appEnvironment.getSystemProperties().put("spring.ai.openai.api-key", binding.getSecret().get("api-key"));
			 appEnvironment.getSystemProperties().put("spring.ai.openai.base-url", binding.getSecret().get("uri"));
            
		   });
        }

        if (profiles.size() > 0) {
            logger.info("Setting service profile " + profiles.get(0));
            appEnvironment.addActiveProfile(profiles.get(0));
        }
    }

    private void validateActiveProfiles(ConfigurableEnvironment appEnvironment) {
        Set<String> validLocalProfiles = profileNameToServiceTags.keySet();

        List<String> serviceProfiles = Stream.of(appEnvironment.getActiveProfiles())
                .filter(validLocalProfiles::contains)
                .collect(Collectors.toList());

        if (serviceProfiles.size() > 1) {
            throw new IllegalStateException("Only one active Spring profile may be set among the following: " +
                    validLocalProfiles.toString() + ". " +
                    "These profiles are active: [" +
                    StringUtils.collectionToCommaDelimitedString(serviceProfiles) + "]");
        }
    }

    private void excludeAutoConfiguration(ConfigurableEnvironment environment) {
        List<String> exclude = new ArrayList<>();
        if (environment.acceptsProfiles(Profiles.of("redis"))) {
            excludeDataSourceAutoConfiguration(exclude);
            excludeMongoAutoConfiguration(exclude);
        } else if (environment.acceptsProfiles(Profiles.of("mongodb"))) {
            excludeDataSourceAutoConfiguration(exclude);
            excludeRedisAutoConfiguration(exclude);
        } else {
            excludeMongoAutoConfiguration(exclude);
            excludeRedisAutoConfiguration(exclude);
        }

        Map<String, Object> properties = Collections.singletonMap("spring.autoconfigure.exclude",
                StringUtils.collectionToCommaDelimitedString(exclude));

        PropertySource<?> propertySource = new MapPropertySource("springMusicAutoConfig", properties);

        environment.getPropertySources().addFirst(propertySource);
    }

    private void excludeDataSourceAutoConfiguration(List<String> exclude) {
        exclude.add(DataSourceAutoConfiguration.class.getName());
    }

    private void excludeMongoAutoConfiguration(List<String> exclude) {
        exclude.addAll(Arrays.asList(
                MongoAutoConfiguration.class.getName(),
                MongoDataAutoConfiguration.class.getName(),
                MongoRepositoriesAutoConfiguration.class.getName()
        ));
    }

    private void excludeRedisAutoConfiguration(List<String> exclude) {
        exclude.addAll(Arrays.asList(
                RedisAutoConfiguration.class.getName(),
                RedisRepositoriesAutoConfiguration.class.getName()
        ));
    }
}
