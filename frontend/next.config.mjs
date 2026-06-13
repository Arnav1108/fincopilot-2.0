/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    unoptimized: true,
  },
  webpack: (config, { isServer }) => {
    config.node = {
      ...config.node,
      __dirname: true,
    }
    return config
  },
}

export default nextConfig
