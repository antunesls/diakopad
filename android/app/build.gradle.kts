plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val releaseStoreFile = System.getenv("DIAKOPAD_SIGNING_STORE_FILE")

android {
    namespace = "org.diakopad.remote"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.diakopad.remote"
        minSdk = 26
        targetSdk = 35
        versionCode = System.getenv("DIAKOPAD_VERSION_CODE")?.toInt() ?: 1
        versionName = System.getenv("DIAKOPAD_VERSION_NAME") ?: "0.1.0"
    }
    signingConfigs {
        if (releaseStoreFile != null) {
            create("release") {
                storeFile = file(releaseStoreFile)
                storePassword = requireNotNull(System.getenv("DIAKOPAD_SIGNING_STORE_PASSWORD"))
                keyAlias = requireNotNull(System.getenv("DIAKOPAD_SIGNING_KEY_ALIAS"))
                keyPassword = requireNotNull(System.getenv("DIAKOPAD_SIGNING_KEY_PASSWORD"))
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            if (releaseStoreFile != null) signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.04.01")
    implementation(composeBom)
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation("org.json:json:20240303")
}
