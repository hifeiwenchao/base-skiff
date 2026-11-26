// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "BaseSkiffNative",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "BaseSkiffNative", targets: ["BaseSkiffNative"])],
    targets: [
        .executableTarget(name: "BaseSkiffNative"),
        .testTarget(name: "BaseSkiffNativeTests", dependencies: ["BaseSkiffNative"])
    ],
    swiftLanguageModes: [.v5]
)
