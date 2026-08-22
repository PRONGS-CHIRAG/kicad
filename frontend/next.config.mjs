/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emits a self-contained server bundle so the Docker image needs no node_modules.
  output: "standalone",
};

export default nextConfig;
